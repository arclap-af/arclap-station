#!/usr/bin/env bash
# Arclap Station bootstrap installer.
#
# One-liner from the README:
#   curl -fsSL https://raw.githubusercontent.com/arclap-af/arclap-station/main/install.sh | sudo bash
#
# Pinned version (recommended for production):
#   curl -fsSL https://raw.githubusercontent.com/arclap-af/arclap-station/main/install.sh \
#     | sudo ARCLAP_VERSION=v0.1.0 bash
#
# This script is idempotent, resumable, and verbose. Every step is wrapped in a
# step() function that prints a banner, runs the action, and continues if the
# action has already completed.

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
readonly ARCLAP_REPO="${ARCLAP_REPO:-arclap-af/arclap-station}"
readonly ARCLAP_VERSION="${ARCLAP_VERSION:-latest}"
readonly ARCLAP_USER="arclap"
readonly ARCLAP_GROUP="arclap"
readonly ARCLAP_HOME="/var/lib/arclap"
readonly ARCLAP_PREFIX="/opt/arclap-station"
readonly ARCLAP_WEBROOT="/var/www/arclap"
readonly ARCLAP_CONFDIR="/etc/arclap"
readonly ARCLAP_LOGDIR="/var/log/arclap"
readonly ARCLAP_PHOTODIR="/media/sdcard/photos"
readonly ARCLAP_SOCKET="/run/arclap-station.sock"
readonly ARCLAP_TMPDIR="/tmp/arclap-install-$$"
# ARCLAP_PYTHON is resolved at runtime in pick_python(); we accept any
# python3 >= 3.11 because the codebase doesn't use anything 3.12-or-later
# specific. Older Pi-OS Bookworm ships 3.11; Ubuntu Noble ships 3.12;
# the post-Noble Ubuntu releases (24.10+, "Oracular"/"Plucky"/"Resolute")
# ship 3.12 or 3.13. Pinning to 3.11 broke installs on those.
ARCLAP_PYTHON=""

# Colour helpers (no-op if stdout is not a TTY).
if [[ -t 1 ]]; then
  readonly C_BOLD="$(printf '\033[1m')"
  readonly C_RED="$(printf '\033[31m')"
  readonly C_GREEN="$(printf '\033[32m')"
  readonly C_YELLOW="$(printf '\033[33m')"
  readonly C_BLUE="$(printf '\033[34m')"
  readonly C_RESET="$(printf '\033[0m')"
else
  readonly C_BOLD=""
  readonly C_RED=""
  readonly C_GREEN=""
  readonly C_YELLOW=""
  readonly C_BLUE=""
  readonly C_RESET=""
fi

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()    { printf "%s\n" "$*"; }
info()   { printf "%s==>%s %s\n" "${C_BLUE}" "${C_RESET}" "$*"; }
ok()     { printf "%s[ok]%s %s\n" "${C_GREEN}" "${C_RESET}" "$*"; }
warn()   { printf "%s[warn]%s %s\n" "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
die()    { printf "%s[fail]%s %s\n" "${C_RED}" "${C_RESET}" "$*" >&2; exit 1; }

step()   {
  local n="$1"; shift
  local label="$1"; shift
  printf "\n%s== Step %s/%s — %s ==%s\n" "${C_BOLD}" "${n}" "${TOTAL_STEPS}" "${label}" "${C_RESET}"
}

TOTAL_STEPS=14

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
cleanup() {
  if [[ -d "${ARCLAP_TMPDIR}" ]]; then
    rm -rf "${ARCLAP_TMPDIR}"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Pre-flight
# ---------------------------------------------------------------------------
preflight() {
  step 1 "Pre-flight checks"

  if [[ "${EUID}" -ne 0 ]]; then
    die "Installer must be run as root. Try: curl -fsSL ... | sudo bash"
  fi
  ok "Running as root"

  # Pi 5 detection — accept stronger boards (CM5, future revisions) by matching
  # "Raspberry Pi 5" in /proc/cpuinfo Model line.
  if [[ -r /proc/cpuinfo ]]; then
    if ! grep -qE "^Model\s*:\s*Raspberry Pi 5" /proc/cpuinfo; then
      if [[ "${ARCLAP_SKIP_HARDWARE_CHECK:-0}" == "1" ]]; then
        warn "Not a Raspberry Pi 5 — continuing because ARCLAP_SKIP_HARDWARE_CHECK=1"
      else
        die "This installer targets the Raspberry Pi 5. Override with ARCLAP_SKIP_HARDWARE_CHECK=1 if you know what you are doing."
      fi
    else
      ok "Detected Raspberry Pi 5"
    fi
  else
    warn "/proc/cpuinfo is unreadable; skipping hardware probe"
  fi

  # OS check: any Debian/Ubuntu-ish aarch64 with apt + Python ≥ 3.11.
  # We don't refuse newer releases — the codebase is forward-compatible
  # with Python 3.12 and 3.13. Just inform the operator what we saw.
  # Sourced in a subshell so the caller's $VERSION isn't clobbered by
  # Ubuntu's own VERSION="26.04 (Resolute Raccoon)".
  if [[ -r /etc/os-release ]]; then
    local os_id os_codename os_pretty
    os_id=$(. /etc/os-release; printf '%s' "${ID:-}")
    os_codename=$(. /etc/os-release; printf '%s' "${VERSION_CODENAME:-}")
    os_pretty=$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-unknown}")
    case "${os_id}" in
      raspbian|debian|ubuntu)
        ok "Detected ${os_pretty} (${os_codename:-no-codename})"
        ;;
      *)
        warn "OS id '${os_id}' is not Debian/Ubuntu-derived. Apt-based install may fail."
        ;;
    esac
  fi

  local arch
  arch="$(uname -m)"
  if [[ "${arch}" != "aarch64" ]]; then
    if [[ "${ARCLAP_SKIP_HARDWARE_CHECK:-0}" == "1" ]]; then
      warn "Architecture is ${arch}, not aarch64. Continuing under override."
    else
      die "Architecture is ${arch}, expected aarch64. Re-flash with the 64-bit Raspberry Pi OS Bookworm image."
    fi
  else
    ok "Architecture aarch64"
  fi

  # Sanity: we need a working internet connection for apt + GitHub.
  if ! curl --silent --head --fail --max-time 10 https://github.com >/dev/null; then
    die "Cannot reach https://github.com — connect this Pi to the internet and re-run."
  fi
  ok "Internet reachable"

  mkdir -p "${ARCLAP_TMPDIR}"
}

# ---------------------------------------------------------------------------
# 2. apt-get install dependencies
# ---------------------------------------------------------------------------
install_apt_deps() {
  step 2 "Installing system dependencies via apt"

  export DEBIAN_FRONTEND=noninteractive

  # Base packages. We pull the distro-default python3 here as a safety
  # net; further down we try to install python3.13 explicitly because
  # several of our pinned C-extension deps (pydantic-core 2.23 via
  # PyO3 0.22, Pillow 10.4) only support Python ≤3.13. Pi-OS Bookworm
  # already ships 3.11 as default; Ubuntu Resolute ships 3.14 default
  # but 3.13 is in universe.
  #
  # Image-format dev headers (zlib/jpeg/tiff/webp) live here so that
  # Pillow can source-build as a last resort.
  local packages=(
    libgphoto2-dev
    gphoto2
    caddy
    avahi-daemon
    avahi-utils
    python3
    python3-venv
    python3-pip
    python3-dev
    usbutils
    ntpsec
    curl
    ca-certificates
    jq
    rsync
    tar
    xz-utils
    libffi-dev
    libssl-dev
    zlib1g-dev
    zram-tools
    ufw
    fail2ban
    logrotate
    libjpeg-dev
    libtiff-dev
    libwebp-dev
    libfreetype-dev
  )

  if ! apt-get update -qq; then
    die "apt-get update failed. Check /var/log/apt/term.log and re-run."
  fi

  # Filter to packages not already installed so re-runs are quick.
  local missing=()
  for pkg in "${packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q "install ok installed"; then
      missing+=("${pkg}")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "All apt packages already installed"
  else
    info "Installing: ${missing[*]}"
    apt-get install -y --no-install-recommends "${missing[@]}"
    ok "Installed ${#missing[@]} package(s)"
  fi

  # NTP — set up time sync so HTTPS certs and audit timestamps are sane.
  if ! systemctl is-active --quiet ntpsec; then
    systemctl enable --now ntpsec || warn "Could not start ntpsec; carrying on."
  fi

  # Best-effort: ensure python3.13 is present. All our pinned C-extension
  # dependencies (pydantic-core, Pillow, bcrypt's Rust backend,
  # cryptography) ship cp313 wheels — fast install, no compilation.
  # cp314 wheels don't exist yet for the versions we pin, and PyO3 0.22
  # (used transitively) hard-caps at 3.13. So if the distro-default is
  # 3.14, we prefer 3.13 explicitly.
  if ! command -v python3.13 >/dev/null 2>&1; then
    if apt-cache show python3.13 >/dev/null 2>&1; then
      info "Installing python3.13 + python3.13-venv (preferred over 3.14 for wheel compatibility)"
      if apt-get install -y --no-install-recommends python3.13 python3.13-venv python3.13-dev; then
        ok "python3.13 installed"
      else
        warn "Could not install python3.13; falling back to system python3."
      fi
    else
      warn "python3.13 not in apt repos; falling back to system python3 (may force slow source builds)."
    fi
  fi

  # Resolve which python3 to actually use (prefers 3.13 → 3.12 → 3.11 → python3).
  pick_python
}

pick_python() {
  if [[ -n "${ARCLAP_PYTHON}" ]]; then
    return
  fi
  local candidates=(python3.13 python3.12 python3.11 python3)
  for c in "${candidates[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then
      local v
      v=$("$c" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
      local major minor
      major="${v%%.*}"; minor="${v##*.}"
      if [[ "${major}" -ge 3 && "${minor}" -ge 11 ]]; then
        ARCLAP_PYTHON="$c"
        ok "Using ${c} (Python ${v})"
        return
      fi
    fi
  done
  die "No suitable Python 3.11+ found. Tried: ${candidates[*]}"
}

# ---------------------------------------------------------------------------
# 3. System user + groups
# ---------------------------------------------------------------------------
create_user() {
  step 3 "Creating system user '${ARCLAP_USER}'"

  if id "${ARCLAP_USER}" >/dev/null 2>&1; then
    ok "User '${ARCLAP_USER}' already exists"
  else
    useradd \
      --system \
      --home-dir "${ARCLAP_HOME}" \
      --create-home \
      --shell /usr/sbin/nologin \
      --comment "Arclap Station service account" \
      "${ARCLAP_USER}"
    ok "Created user '${ARCLAP_USER}'"
  fi

  # Group membership:
  #   plugdev         — USB device-node access (DSLR over PTP)
  #   video           — V4L (UVC webcam fallback)
  #   dialout         — serial cameras (rare, kept for completeness)
  #   systemd-journal — read journalctl as the service user. Required
  #                     for the cockpit's Settings → Logs tab; without
  #                     it `journalctl -u arclap-station` from the
  #                     service-owned user returns "No journal files
  #                     were opened due to insufficient permissions"
  #                     and the cockpit shows "0 lines".
  for g in plugdev video dialout systemd-journal; do
    if getent group "${g}" >/dev/null 2>&1; then
      if ! id -nG "${ARCLAP_USER}" | tr ' ' '\n' | grep -qx "${g}"; then
        usermod -a -G "${g}" "${ARCLAP_USER}"
        ok "Added ${ARCLAP_USER} to group ${g}"
      fi
    fi
  done
}

# ---------------------------------------------------------------------------
# 4. Filesystem layout
# ---------------------------------------------------------------------------
create_layout() {
  step 4 "Creating directory layout"

  local dirs=(
    "${ARCLAP_PREFIX}"
    "${ARCLAP_PREFIX}/releases"
    "${ARCLAP_WEBROOT}"
    "${ARCLAP_CONFDIR}"
    "${ARCLAP_CONFDIR}/destinations"
    "${ARCLAP_HOME}"
    "${ARCLAP_LOGDIR}"
    "${ARCLAP_PHOTODIR}"
  )

  for d in "${dirs[@]}"; do
    install -d -m 0755 "${d}"
  done

  # Ownership: arclap owns its state, photos and logs. Caddy reads webroot.
  chown -R "${ARCLAP_USER}:${ARCLAP_GROUP}" \
    "${ARCLAP_HOME}" "${ARCLAP_CONFDIR}" "${ARCLAP_LOGDIR}" "${ARCLAP_PHOTODIR}" \
    "${ARCLAP_PREFIX}"
  chmod 0750 "${ARCLAP_CONFDIR}"   # auth secrets live here

  ok "Layout ready"
}

# ---------------------------------------------------------------------------
# 5. Fetch release artifacts
# ---------------------------------------------------------------------------
fetch_release() {
  step 5 "Fetching release artifacts (${ARCLAP_VERSION})"

  local base_url
  if [[ "${ARCLAP_VERSION}" == "latest" ]]; then
    base_url="https://github.com/${ARCLAP_REPO}/releases/latest/download"
  else
    base_url="https://github.com/${ARCLAP_REPO}/releases/download/${ARCLAP_VERSION}"
  fi

  local frontend_tar="arclap-station-frontend.tar.gz"

  # Wheels land in ARCLAP_TMPDIR/wheels/ with their PEP 427-compliant
  # filenames intact (pip rejects non-conforming names like "wheel.whl").
  # install_backend() and update_inplace() use arclap_wheel_path() to
  # locate whatever ended up there, regardless of source.
  mkdir -p "${ARCLAP_TMPDIR}/wheels"

  pushd "${ARCLAP_TMPDIR}" >/dev/null

  # Try pre-built release artifacts first. If they're missing (release
  # pipeline hasn't published this tag yet, or we're tracking an
  # unreleased commit), fall through to build-from-source. We probe a
  # handful of plausible PEP 427 filenames because the release matrix
  # may publish under any of them and we don't get a directory listing.
  local version="${ARCLAP_VERSION#v}"
  local wheel_candidates=(
    "arclap_station-${version}-py3-none-any.aarch64.whl"
    "arclap_station-${version}-py3-none-any.whl"
    "arclap_station-${version}-cp313-cp313-linux_aarch64.whl"
    "arclap_station-${version}-cp312-cp312-linux_aarch64.whl"
    "arclap_station-${version}-cp311-cp311-linux_aarch64.whl"
  )
  local got_wheel=0 got_frontend=0
  for name in "${wheel_candidates[@]}"; do
    if curl --silent --location --fail --output "wheels/${name}" \
        "${base_url}/${name}" 2>/dev/null; then
      got_wheel=1
      ok "Downloaded ${name} ($(stat -c '%s' "wheels/${name}") bytes)"
      break
    fi
  done
  if curl --silent --location --fail --output "${frontend_tar}" \
      "${base_url}/${frontend_tar}" 2>/dev/null; then
    got_frontend=1
    ok "Downloaded frontend bundle ($(stat -c '%s' "${frontend_tar}") bytes)"
  fi
  popd >/dev/null

  if [[ "${got_wheel}" == "1" && "${got_frontend}" == "1" ]]; then
    return
  fi

  # ----- Build from source fallback -----
  warn "Pre-built artifacts missing — building from source."
  info "Adds ~3 min on a Pi 5: clone, pip wheel, npm build."

  # Pull in build deps not in the base list. Rust + cargo are needed
  # because several pinned C-extension deps (pydantic-core, bcrypt's
  # backend, cryptography) only ship wheels up to cp313 — on bleeding-edge
  # Pythons (3.14+) pip falls back to source builds that need rustc.
  export DEBIAN_FRONTEND=noninteractive
  local build_deps=(git build-essential nodejs npm rustc cargo pkg-config)
  apt-get install -y --no-install-recommends "${build_deps[@]}" \
      || die "Could not install source-build deps (${build_deps[*]})"

  local src="${ARCLAP_TMPDIR}/src"
  if [[ -d "${src}/.git" ]]; then
    git -C "${src}" fetch --depth 1 origin "${ARCLAP_VERSION}" 2>/dev/null \
      || git -C "${src}" fetch --depth 1 origin main
    git -C "${src}" checkout FETCH_HEAD
  else
    git clone --depth 1 "https://github.com/${ARCLAP_REPO}.git" "${src}" \
        || die "Could not clone source from https://github.com/${ARCLAP_REPO}.git"
  fi
  ok "Cloned source"

  # Build the wheel.
  local build_venv="${ARCLAP_TMPDIR}/build-venv"
  if [[ ! -x "${build_venv}/bin/python" ]]; then
    "${ARCLAP_PYTHON}" -m venv "${build_venv}"
  fi
  "${build_venv}/bin/pip" install --upgrade --quiet pip build
  # `python -m build` writes the wheel into wheels/ with its PEP 427
  # filename (e.g. arclap_station-0.1.0-py3-none-any.whl). We DO NOT
  # rename it — pip refuses non-conforming names.
  "${build_venv}/bin/python" -m build --wheel --outdir "${ARCLAP_TMPDIR}/wheels" "${src}/backend" \
      || die "Wheel build failed; see /var/log/arclap-install.log"
  local built_wheel
  built_wheel="$(ls -1 "${ARCLAP_TMPDIR}/wheels/"arclap_station-*.whl 2>/dev/null | head -n1)"
  [[ -z "${built_wheel}" ]] && die "Wheel build produced no .whl in ${ARCLAP_TMPDIR}/wheels/"
  ok "Built wheel from source: $(basename "${built_wheel}") ($(stat -c '%s' "${built_wheel}") bytes)"

  # Build the frontend.
  pushd "${src}/frontend" >/dev/null
  npm ci --silent || npm install --silent
  npm run build --silent
  tar -C dist -czf "${ARCLAP_TMPDIR}/${frontend_tar}" .
  popd >/dev/null
  ok "Built frontend bundle ($(stat -c '%s' "${ARCLAP_TMPDIR}/${frontend_tar}") bytes)"
}

# ---------------------------------------------------------------------------
# 6. Install backend (venv + wheel)
# ---------------------------------------------------------------------------
install_backend() {
  step 6 "Installing backend Python package"

  local venv="${ARCLAP_PREFIX}/venv"

  # Detect a venv built with a different Python — happens when an
  # earlier install attempt picked the distro-default 3.14 and a later
  # run picks 3.13. Recreate from scratch instead of trying to graft.
  local recreate=0
  if [[ -x "${venv}/bin/python3" ]]; then
    local venv_py target_py
    venv_py="$("${venv}/bin/python3" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo unknown)"
    target_py="$("${ARCLAP_PYTHON}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    if [[ "${venv_py}" != "${target_py}" ]]; then
      warn "Existing venv uses Python ${venv_py}, installer is using ${target_py}. Recreating."
      recreate=1
    else
      ok "venv already exists (Python ${venv_py})"
    fi
  else
    recreate=1
  fi

  if [[ "${recreate}" == "1" ]]; then
    rm -rf "${venv}"
    "${ARCLAP_PYTHON}" -m venv "${venv}"
    ok "Created venv at ${venv} using ${ARCLAP_PYTHON}"
  fi

  "${venv}/bin/pip" install --upgrade --quiet pip setuptools wheel

  # Install the wheel (PEP 427 filename preserved by fetch_release()).
  # --force-reinstall lets re-runs upgrade in place. We deliberately do
  # NOT pass --quiet here — installing ~80 packages on a fresh Pi 5
  # takes 5+ minutes, and a quiet pip leaves the operator wondering
  # whether the installer is stuck. Real-time progress is worth the noise.
  #
  # PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1: belt-and-braces. If any
  # transitive dep still pulls in an old PyO3 (<0.23) on Python ≥3.14,
  # this env var bypasses PyO3's interpreter-version refusal and uses
  # the stable ABI for forward compat. Harmless when not needed.
  local wheel
  wheel="$(arclap_wheel_path)"
  PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 \
    "${venv}/bin/pip" install --upgrade --force-reinstall "${wheel}"
  ok "Installed $(basename "${wheel}") into venv"

  # python-gphoto2 is the binding to libgphoto2 for DSLR control. It's
  # an OPTIONAL extra in pyproject.toml so the base wheel stays portable
  # to dev machines, but on a real Pi we always want it. Failing to
  # install drops us to the MockCamera adapter (which returns fake EOS
  # R6 data even when a real Canon is plugged in — confusing!).
  info "Installing python-gphoto2 (libgphoto2 binding)"
  if "${venv}/bin/pip" install --upgrade "gphoto2>=2.5.0"; then
    ok "python-gphoto2 installed"
  else
    warn "python-gphoto2 install failed — camera will fall back to mock mode"
  fi

  # Ensure the venv is owned by arclap so the service can read it (root
  # ownership from a prior pip-as-root run breaks bcrypt key loads etc.).
  chown -R "${ARCLAP_USER}:${ARCLAP_GROUP}" "${venv}"

  # CLI shim — single shared name regardless of internal naming.
  if [[ -x "${venv}/bin/arclap-station" ]]; then
    ln -sf "${venv}/bin/arclap-station" /usr/local/bin/arclap-station
    ok "Linked /usr/local/bin/arclap-station"
  else
    warn "Wheel did not provide arclap-station console script; check pyproject.toml [project.scripts]."
  fi

  chown -R "${ARCLAP_USER}:${ARCLAP_GROUP}" "${ARCLAP_PREFIX}"
}

# ---------------------------------------------------------------------------
# 7. Install frontend
# ---------------------------------------------------------------------------
install_frontend() {
  step 7 "Installing frontend bundle"

  # Clean install — frontend assets are immutable and small.
  rm -rf "${ARCLAP_WEBROOT:?}"/*
  tar -xzf "${ARCLAP_TMPDIR}/arclap-station-frontend.tar.gz" -C "${ARCLAP_WEBROOT}"
  chown -R root:root "${ARCLAP_WEBROOT}"
  find "${ARCLAP_WEBROOT}" -type d -exec chmod 0755 {} \;
  find "${ARCLAP_WEBROOT}" -type f -exec chmod 0644 {} \;
  ok "Frontend deployed to ${ARCLAP_WEBROOT}"
}

# ---------------------------------------------------------------------------
# 8. udev rules + remove conflicting gvfs gphoto2 module
# ---------------------------------------------------------------------------
install_udev() {
  step 8 "Installing udev rules"

  local assets
  assets="$(arclap_assets_dir)"
  local rule_src="${assets}/udev/50-arclap-camera.rules"
  local rule_dst="/etc/udev/rules.d/50-arclap-camera.rules"

  if [[ -f "${rule_src}" ]]; then
    install -m 0644 "${rule_src}" "${rule_dst}"
  else
    # Fallback inline rule, in case the installer runs without the repo around it.
    cat > "${rule_dst}" <<'RULE'
# Arclap Station udev rules (fallback inline copy).
# Grant plugdev access to typical DSLR vendors over USB.
SUBSYSTEM=="usb", ATTR{idVendor}=="04a9", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="04b0", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="054c", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="04cb", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="2207", MODE="0660", GROUP="plugdev"
RULE
  fi

  # Layer in libgphoto2's official rule list if we can — it covers hundreds of bodies.
  if command -v print-camera-list >/dev/null 2>&1; then
    if print-camera-list udev-rules version 201 \
        group plugdev mode 0660 > /etc/udev/rules.d/40-libgphoto2.rules 2>/dev/null; then
      ok "Generated /etc/udev/rules.d/40-libgphoto2.rules from libgphoto2"
    fi
  elif [[ -x /usr/lib/aarch64-linux-gnu/libgphoto2/print-camera-list ]]; then
    /usr/lib/aarch64-linux-gnu/libgphoto2/print-camera-list udev-rules version 201 \
      group plugdev mode 0660 > /etc/udev/rules.d/40-libgphoto2.rules 2>/dev/null || true
  fi

  # Defuse gvfs-gphoto2 — it grabs the camera the moment it appears and starves us.
  systemctl mask gvfs-gphoto2-volume-monitor.service 2>/dev/null || true
  systemctl mask gvfs-daemon.service 2>/dev/null || true
  if dpkg-query -W -f='${Status}' gvfs-backends 2>/dev/null | grep -q "install ok installed"; then
    warn "gvfs-backends is installed; masking the volume monitor. Remove the package for stronger isolation."
  fi

  udevadm control --reload-rules
  udevadm trigger --action=change
  ok "udev rules active"
}

# ---------------------------------------------------------------------------
# 9. systemd units
# ---------------------------------------------------------------------------
install_systemd() {
  step 9 "Installing systemd units"

  local assets src
  assets="$(arclap_assets_dir)"
  src="${assets}/systemd"

  if [[ ! -d "${src}" ]]; then
    die "Asset tree at ${assets} has no systemd/ directory — repo layout changed?"
  fi

  install -m 0644 "${src}/arclap-station.service"           /etc/systemd/system/arclap-station.service
  install -m 0644 "${src}/arclap-station.socket"            /etc/systemd/system/arclap-station.socket
  install -m 0644 "${src}/arclap-watchdog.service"          /etc/systemd/system/arclap-watchdog.service
  install -m 0644 "${src}/arclap-watchdog.timer"            /etc/systemd/system/arclap-watchdog.timer
  install -m 0644 "${src}/arclap-camera-watchdog.service"   /etc/systemd/system/arclap-camera-watchdog.service
  install -m 0644 "${src}/arclap-camera-watchdog.timer"     /etc/systemd/system/arclap-camera-watchdog.timer
  install -m 0644 "${src}/arclap-retention.service"         /etc/systemd/system/arclap-retention.service
  install -m 0644 "${src}/arclap-retention.timer"           /etc/systemd/system/arclap-retention.timer
  install -m 0644 "${src}/arclap-usb3-disable.service"      /etc/systemd/system/arclap-usb3-disable.service
  # arclap-uploader.service is deliberately NOT deployed — its
  # ExecStart calls a non-existent `arclap-station uploader` subcommand.
  # The uploader queue runs in-process via FastAPI lifespan, so the
  # standalone unit is redundant. Defensively remove any stale copy.
  rm -f /etc/systemd/system/arclap-uploader.service

  # --- v0.9 stability hardening: watchdog + SD-card longevity ---------
  # Pi hardware watchdog: systemd pets /dev/watchdog; if systemd itself
  # hangs for 15s the BCM2712 hardware watchdog hard-reboots the Pi.
  # (The per-service software watchdog is in arclap-station.service via
  # WatchdogSec — that catches a hung app; this catches a hung system.)
  mkdir -p /etc/systemd/system.conf.d
  cat > /etc/systemd/system.conf.d/10-arclap-watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOF

  # journald: cap size so logs don't grind the SD card over a long
  # deployment (rotation instead of unbounded growth).
  mkdir -p /etc/systemd/journald.conf.d
  cat > /etc/systemd/journald.conf.d/10-arclap.conf <<'EOF'
[Journal]
SystemMaxUse=100M
SystemMaxFileSize=20M
RuntimeMaxUse=50M
EOF
  systemctl restart systemd-journald 2>/dev/null || true

  # noatime on the root fs: eliminates a metadata write on every file
  # read (serving thumbnails, reading config). Apply live + persist in
  # fstab. Only touch the root line, and only if noatime isn't already
  # present, so we never corrupt a working fstab.
  mount -o remount,noatime / 2>/dev/null || warn "could not remount / noatime (continuing)"
  if [[ -f /etc/fstab ]] && ! awk '$2=="/"{print $4}' /etc/fstab | grep -q noatime; then
    cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d-%H%M%S)"
    # Append ',noatime' to the options field (col 4) of the / mount only.
    awk 'BEGIN{OFS="\t"} $2=="/" && $4 !~ /noatime/ {$4=$4",noatime"} {print}' \
      /etc/fstab > /etc/fstab.arclap.tmp && mv /etc/fstab.arclap.tmp /etc/fstab
    ok "added noatime to / in fstab (backup kept)"
  fi

  systemctl daemon-reexec 2>/dev/null || true
  systemctl daemon-reload
  ok "systemd units installed + stability hardening applied"
}

# ---------------------------------------------------------------------------
# 10. Caddyfile
# ---------------------------------------------------------------------------
install_caddy() {
  step 10 "Configuring Caddy reverse proxy"

  local assets serial tmpl
  assets="$(arclap_assets_dir)"
  serial="$(short_serial)"
  tmpl="${assets}/caddy/Caddyfile.template"

  if [[ ! -f "${tmpl}" ]]; then
    die "Asset tree at ${assets} has no caddy/Caddyfile.template"
  fi

  sed -e "s|\${SERIAL}|${serial}|g" "${tmpl}" > /etc/caddy/Caddyfile
  chmod 0644 /etc/caddy/Caddyfile

  ok "Caddyfile written (hostname: arclap-st-${serial}.local)"
}

# ---------------------------------------------------------------------------
# 11. Avahi mDNS service record
# ---------------------------------------------------------------------------
install_avahi() {
  step 11 "Publishing Avahi/mDNS service record"

  local assets serial src
  assets="$(arclap_assets_dir)"
  serial="$(short_serial)"
  src="${assets}/avahi/arclap-station.service"

  if [[ ! -f "${src}" ]]; then
    die "Asset tree at ${assets} has no avahi/arclap-station.service"
  fi

  install -m 0644 "${src}" /etc/avahi/services/arclap-station.service

  # Set the system hostname so the .local name matches.
  local desired="arclap-st-${serial}"
  if [[ "$(hostnamectl --static)" != "${desired}" ]]; then
    hostnamectl set-hostname "${desired}"
    if ! grep -q "${desired}" /etc/hosts; then
      printf "127.0.1.1\t%s\n" "${desired}" >> /etc/hosts
    fi
    ok "Hostname set to ${desired}"
  fi

  systemctl restart avahi-daemon || warn "avahi-daemon restart failed; mDNS may be unavailable until reboot"
  ok "Avahi advertising arclap-st-${serial}.local on _arclap._tcp"
}

# ---------------------------------------------------------------------------
# 12. Enable services
# ---------------------------------------------------------------------------
enable_services() {
  step 12 "Enabling services"

  # Defensively disable arclap-station.socket if a previous (broken)
  # install enabled it. The current architecture uses plain TCP, not
  # systemd socket activation, so the .socket unit is a no-op.
  systemctl disable --now arclap-station.socket 2>/dev/null || true

  # We don't bail on individual enable failures — we want the user to
  # see WHICH service failed, not just a generic "Job failed" from
  # systemctl. Errors are tallied and journalctl-printed at the end.
  #
  # NOTE: arclap-uploader.service is NOT enabled. The uploader queue
  # runs inside the FastAPI process (see backend lifespan in main.py),
  # so the standalone unit is redundant AND its ExecStart references a
  # CLI subcommand that doesn't exist. Defensively disable it in case
  # an earlier install left it enabled.
  systemctl disable --now arclap-uploader.service 2>/dev/null || true

  # arclap-watchdog: now safe to enable (v0.2 rewrite probes loopback HTTP
  # instead of HTTPS-via-Caddy, includes a 60s startup grace, and the
  # state file is reset on restart instead of accumulating forever).
  # Defensively clear any stale fail counter from the old version.
  rm -f /run/arclap-watchdog.fail

  local units=(
    caddy
    avahi-daemon
    arclap-usb3-disable.service
    arclap-station.service
    arclap-camera-watchdog.timer
    arclap-retention.timer
    arclap-watchdog.timer
  )
  local failed=()
  for u in "${units[@]}"; do
    if systemctl enable --now "${u}"; then
      ok "Enabled ${u}"
    else
      warn "systemctl enable --now ${u} returned non-zero"
      failed+=("${u}")
    fi
  done

  # Give the FastAPI service a moment to settle.
  sleep 5

  # arclap-station.service is the one that matters most. If it's down,
  # dump its logs and abort so the operator can diagnose.
  if ! systemctl is-active --quiet arclap-station.service; then
    warn "arclap-station.service is not active. Recent logs:"
    journalctl -u arclap-station -n 60 --no-pager || true
    if [[ ${#failed[@]} -gt 0 ]]; then
      warn "Other units that failed to enable: ${failed[*]}"
      for u in "${failed[@]}"; do
        printf "\n--- journalctl -u %s -n 30 ---\n" "${u}"
        journalctl -u "${u}" -n 30 --no-pager || true
      done
    fi
    die "arclap-station.service failed to start. See logs above and consult docs/troubleshooting.md."
  fi

  if [[ ${#failed[@]} -gt 0 ]]; then
    warn "Non-critical units that failed to enable: ${failed[*]} — install will continue."
  fi
  ok "All services active"
}

# ---------------------------------------------------------------------------
# 13. Success banner
# ---------------------------------------------------------------------------
print_banner() {
  step 13 "Success"

  local serial ipv4 hostname
  serial="$(short_serial)"
  hostname="arclap-st-${serial}"
  ipv4="$(hostname -I 2>/dev/null | awk '{print $1}')"
  : "${ipv4:=unknown}"

  printf "\n"
  printf "%s┌────────────────────────────────────────────────────────────┐%s\n" "${C_GREEN}" "${C_RESET}"
  printf "%s│  Arclap Station is installed and running.                  │%s\n" "${C_GREEN}" "${C_RESET}"
  printf "%s└────────────────────────────────────────────────────────────┘%s\n" "${C_GREEN}" "${C_RESET}"
  printf "\n"
  printf "  Open on the same LAN:   %shttps://%s.local/%s\n" "${C_BOLD}" "${hostname}" "${C_RESET}"
  printf "  IPv4 fallback:          %shttps://%s/%s\n" "${C_BOLD}" "${ipv4}" "${C_RESET}"
  printf "  Serial:                 %s\n" "${serial}"
  printf "\n"
  printf "  First-boot wizard runs automatically the first time you open the URL.\n"
  printf "  Trust the self-signed certificate to continue (HSTS pinned afterwards).\n"
  printf "\n"
  printf "  Logs:        sudo journalctl -fu arclap-station\n"
  printf "  Status:      sudo systemctl status arclap-station\n"
  printf "  Update:      sudo arclap-station update\n"
  printf "  Uninstall:   sudo arclap-station uninstall\n"
  printf "\n"
}

# ---------------------------------------------------------------------------
# 14. uninstall + update subcommand wiring
# ---------------------------------------------------------------------------
install_self() {
  step 14 "Wiring install.sh into /usr/local/sbin for uninstall/update"

  # Copy install.sh to a stable location so the operator can run
  # `sudo /usr/local/sbin/arclap-station-installer uninstall` / `update`
  # after the wheel rotates. We FAIL the install if we can't land a
  # real installer here — the operator-facing recovery path depends on it.
  local assets src=""
  assets="$(arclap_assets_dir)"
  if [[ -f "${assets}/install.sh" ]]; then
    src="${assets}/install.sh"
  elif [[ -f "$0" && "$(basename "$0")" == "install.sh" ]]; then
    src="$0"
  fi
  if [[ -z "${src}" ]]; then
    die "Could not locate install.sh source for self-copy. The asset tree at ${assets} must contain install.sh. Update/uninstall depend on it — refusing to finish with the recovery path broken."
  fi
  install -m 0755 "${src}" /usr/local/sbin/arclap-station-installer
  ok "Installer self-copied to /usr/local/sbin/arclap-station-installer (source: ${src})"
}

# ---------------------------------------------------------------------------
# 8b. OS hardening drop-ins (audit-driven: kernel watchdog, swap, journald
#     limits, SSH lockdown, firewall, logrotate). Step number is between
#     8 and 9 of the install banner so it pollutes the existing flow least.
#     This is its own callable function — install() invokes it after udev.
# ---------------------------------------------------------------------------
install_os_hardening() {
  step 8 "Applying OS hardening (kernel watchdog, swap, journald, SSH, firewall)"

  # 1. Kernel hardware watchdog. Pi 5 firmware enables /dev/watchdog when
  #    dtparam=watchdog=on; systemd-as-PID-1 pets it when configured.
  install -d -m 0755 /etc/systemd/system.conf.d
  cat > /etc/systemd/system.conf.d/10-arclap-watchdog.conf <<EOF
[Manager]
RuntimeWatchdogSec=30
RebootWatchdogSec=2min
EOF
  systemctl daemon-reexec || true

  # 2. zram-backed swap (1 GB) — OOM safety net on the 8 GB Pi 5.
  install -d -m 0755 /etc/default
  cat > /etc/default/zramswap <<'EOF'
# Arclap Station: zram swap configuration
ALGO=zstd
PERCENT=12
PRIORITY=100
EOF
  systemctl enable --now zramswap 2>/dev/null || true

  # 3. journald limits — without this the journal eats 10% of root forever.
  install -d -m 0755 /etc/systemd/journald.conf.d
  cat > /etc/systemd/journald.conf.d/10-arclap.conf <<EOF
[Journal]
SystemMaxUse=500M
SystemKeepFree=2G
MaxFileSec=1week
RateLimitIntervalSec=30s
RateLimitBurst=10000
EOF
  systemctl kill --kill-who=main --signal=SIGUSR2 systemd-journald 2>/dev/null || true

  # 4. logrotate for /var/log/arclap (our service emits to journal by
  #    default, but any local handler that writes a .log file is covered).
  install -d -m 0755 /etc/logrotate.d
  cat > /etc/logrotate.d/arclap <<'EOF'
/var/log/arclap/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su root root
}
EOF

  # 5. SSH hardening drop-in.
  install -d -m 0755 /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/99-arclap.conf <<'EOF'
# Arclap Station — field-deployment SSH lockdown.
PasswordAuthentication no
PermitRootLogin no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

  # 6. fail2ban default sshd jail (zero config — distro defaults are fine).
  systemctl enable --now fail2ban 2>/dev/null || true

  # 7. ufw firewall: allow ssh, http, https, mDNS, then default-deny.
  if command -v ufw >/dev/null 2>&1; then
    ufw --force reset >/dev/null 2>&1 || true
    ufw allow 22/tcp >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw allow 5353/udp >/dev/null 2>&1 || true
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
  fi

  # 8. resolved fallback DNS so a flaky DHCP DNS doesn't blackhole us.
  install -d -m 0755 /etc/systemd/resolved.conf.d
  cat > /etc/systemd/resolved.conf.d/99-arclap.conf <<'EOF'
[Resolve]
FallbackDNS=9.9.9.9 1.1.1.1 8.8.8.8
DNSStubListener=yes
EOF
  systemctl restart systemd-resolved 2>/dev/null || true

  # 9. systemd-time-wait-sync — the app refuses to start until clock is sane.
  systemctl enable systemd-time-wait-sync.service 2>/dev/null || true

  # 10. polkit rule: let the arclap user restart specific services + reboot
  #     the system without sudo. The Settings → Danger Zone actions in the
  #     cockpit rely on this; without it `systemctl restart` from inside the
  #     FastAPI process (running as `arclap`) silently fails.
  install -d -m 0755 /etc/polkit-1/rules.d
  cat > /etc/polkit-1/rules.d/50-arclap.rules <<'POLKIT'
// Arclap Station — let the `arclap` system user manage its own services
// and trigger reboots from the cockpit, without sudo.
polkit.addRule(function(action, subject) {
    var allowedUnits = [
        "arclap-station.service",
        "arclap-camera-watchdog.timer",
        "arclap-camera-watchdog.service",
        "arclap-retention.timer",
        "arclap-retention.service",
        "arclap-watchdog.timer",
        "arclap-watchdog.service",
        "arclap-usb3-disable.service",
        "arclap-backup.timer",
        "arclap-backup.service",
        "arclap-integrity.timer",
        "arclap-integrity.service",
        "arclap-timelapse.timer",
        "arclap-timelapse.service",
        "caddy.service",
        "avahi-daemon.service",
        // v0.9 — needed for the Network tab DNS / NTP editors.
        "systemd-resolved.service",
        "systemd-timesyncd.service",
    ];
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == "arclap") {
        var unit = action.lookup("unit");
        if (allowedUnits.indexOf(unit) >= 0) {
            return polkit.Result.YES;
        }
    }
    // Allow `arclap` to trigger reboot / power-off (Factory Reset path).
    if ((action.id == "org.freedesktop.login1.reboot" ||
         action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||
         action.id == "org.freedesktop.login1.power-off" ||
         action.id == "org.freedesktop.login1.power-off-multiple-sessions") &&
        subject.user == "arclap") {
        return polkit.Result.YES;
    }
    // v0.9 — NetworkManager actions for the cockpit's Network tab
    // (ethernet IP config, WiFi connect/forget, profile modify).
    if ((action.id == "org.freedesktop.NetworkManager.settings.modify.system" ||
         action.id == "org.freedesktop.NetworkManager.settings.modify.own" ||
         action.id == "org.freedesktop.NetworkManager.network-control" ||
         action.id == "org.freedesktop.NetworkManager.enable-disable-network" ||
         action.id == "org.freedesktop.NetworkManager.enable-disable-wifi") &&
        subject.user == "arclap") {
        return polkit.Result.YES;
    }
    // v0.9 — hostnamectl set-hostname for the cockpit's hostname editor.
    if ((action.id == "org.freedesktop.hostname1.set-hostname" ||
         action.id == "org.freedesktop.hostname1.set-static-hostname") &&
        subject.user == "arclap") {
        return polkit.Result.YES;
    }
    // v0.9 — timedatectl set-ntp/timezone (the timezone Select in
    // Settings → General already exists; this lets it actually persist).
    if ((action.id == "org.freedesktop.timedate1.set-ntp" ||
         action.id == "org.freedesktop.timedate1.set-timezone" ||
         action.id == "org.freedesktop.timedate1.set-time") &&
        subject.user == "arclap") {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
POLKIT
  chmod 0644 /etc/polkit-1/rules.d/50-arclap.rules
  # polkit watches this directory; restart for the rule to take effect.
  systemctl restart polkit 2>/dev/null || true

  ok "OS hardening applied"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
arclap_wheel_path() {
  # Locate the wheel produced by fetch_release(). Lives in
  # ${ARCLAP_TMPDIR}/wheels/ with its PEP 427-compliant filename intact,
  # regardless of whether it was downloaded from a release or built from
  # source. Exits the installer if no wheel was produced.
  local wheel
  wheel="$(ls -1 "${ARCLAP_TMPDIR}/wheels/"arclap_station-*.whl 2>/dev/null | head -n1)"
  if [[ -z "${wheel}" ]]; then
    die "No wheel found under ${ARCLAP_TMPDIR}/wheels/ — fetch_release() did not produce one."
  fi
  printf '%s' "${wheel}"
}

ARCLAP_ASSETS_DIR=""
arclap_assets_dir() {
  # Locate the directory containing systemd/, udev/, caddy/, avahi/,
  # packaging/ and install.sh. When the installer is run via
  # `curl | sudo bash`, $0 is just `bash` and there are no files
  # alongside it — so we search in priority order:
  #   1. The script's own directory (works for local clones).
  #   2. ${ARCLAP_TMPDIR}/src — fetch_release() already cloned the
  #      repo here when taking the build-from-source path.
  #   3. Clone the repo on-demand into ${ARCLAP_TMPDIR}/src.
  # The result is cached in ARCLAP_ASSETS_DIR so callers don't re-clone.
  if [[ -n "${ARCLAP_ASSETS_DIR}" && -d "${ARCLAP_ASSETS_DIR}/systemd" ]]; then
    printf '%s' "${ARCLAP_ASSETS_DIR}"
    return
  fi

  local script_dir
  script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || script_dir=""
  if [[ -n "${script_dir}" && -d "${script_dir}/systemd" ]]; then
    ARCLAP_ASSETS_DIR="${script_dir}"
    printf '%s' "${ARCLAP_ASSETS_DIR}"
    return
  fi

  if [[ -d "${ARCLAP_TMPDIR}/src/systemd" ]]; then
    ARCLAP_ASSETS_DIR="${ARCLAP_TMPDIR}/src"
    printf '%s' "${ARCLAP_ASSETS_DIR}"
    return
  fi

  # Final fallback: clone the repo. This path is taken when:
  #   - The user ran `curl | bash` (so $0 has no surrounding files), AND
  #   - fetch_release() got pre-built artifacts (so it didn't clone), AND
  #   - We need systemd/udev/caddy/avahi files now.
  info "Asset directories not found locally; cloning repo for systemd/udev/caddy/avahi/packaging files" >&2
  if ! command -v git >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends git >/dev/null 2>&1 \
      || die "git required to fetch installer assets"
  fi
  local src="${ARCLAP_TMPDIR}/src"
  if [[ ! -d "${src}/.git" ]]; then
    git clone --depth 1 "https://github.com/${ARCLAP_REPO}.git" "${src}" >/dev/null 2>&1 \
      || die "Could not clone ${ARCLAP_REPO} to fetch assets"
  fi
  if [[ ! -d "${src}/systemd" ]]; then
    die "Cloned repo has no systemd/ — repo layout changed unexpectedly. Check ${ARCLAP_REPO}."
  fi
  ARCLAP_ASSETS_DIR="${src}"
  printf '%s' "${ARCLAP_ASSETS_DIR}"
}

short_serial() {
  # 8 lowercase hex chars from the CPU serial — fall back to mac address.
  local serial=""
  if [[ -r /proc/cpuinfo ]]; then
    serial="$(awk -F: '/^Serial/ { gsub(/ /, "", $2); print tolower($2); exit }' /proc/cpuinfo)"
  fi
  if [[ -z "${serial}" ]]; then
    serial="$(ip link show 2>/dev/null \
      | awk '/link\/ether/ { gsub(/:/, "", $2); print tolower($2); exit }')"
  fi
  if [[ -z "${serial}" ]]; then
    serial="$(date +%s | sha256sum | cut -c1-8)"
  fi
  printf "%s" "${serial: -8}"
}

# ---------------------------------------------------------------------------
# uninstall + update entry points
# ---------------------------------------------------------------------------
uninstall_all() {
  local purge="${1:-no}"
  info "Stopping services"
  systemctl disable --now arclap-station.service arclap-station.socket \
                          arclap-uploader.service arclap-watchdog.timer \
                          arclap-watchdog.service 2>/dev/null || true

  if [[ "${purge}" != "--purge" ]]; then
    if [[ -d "${ARCLAP_PHOTODIR}" ]] && [[ "$(find "${ARCLAP_PHOTODIR}" -type f | head -n1)" != "" ]]; then
      die "Captured photos exist under ${ARCLAP_PHOTODIR}. Re-run with --purge to delete them."
    fi
  fi

  rm -f /etc/systemd/system/arclap-*.service /etc/systemd/system/arclap-*.socket \
        /etc/systemd/system/arclap-*.timer
  rm -f /etc/udev/rules.d/50-arclap-camera.rules /etc/udev/rules.d/40-libgphoto2.rules
  rm -f /etc/avahi/services/arclap-station.service
  rm -f /etc/caddy/Caddyfile
  rm -f /usr/local/bin/arclap-station /usr/local/sbin/arclap-station-installer
  # NOTE: ARCLAP_CONFDIR (/etc/arclap) holds auth.json (the PIN) and
  # dest.key (the envelope key that decrypts every destination secret).
  # A plain uninstall must NOT delete it — otherwise the message below
  # lies ("State preserved") and a reinstall comes back with no PIN and
  # undecryptable destination credentials. It's removed ONLY on --purge.
  rm -rf "${ARCLAP_PREFIX}" "${ARCLAP_WEBROOT}" "${ARCLAP_LOGDIR}"
  if [[ "${purge}" == "--purge" ]]; then
    rm -rf "${ARCLAP_HOME}" "${ARCLAP_PHOTODIR}" "${ARCLAP_CONFDIR}"
  fi

  systemctl daemon-reload
  udevadm control --reload-rules

  ok "Arclap Station removed."
  if [[ "${purge}" != "--purge" ]]; then
    info "State preserved under ${ARCLAP_HOME}, ${ARCLAP_PHOTODIR} and ${ARCLAP_CONFDIR} (PIN + secrets). Re-run with --purge to delete."
  fi
}

update_inplace() {
  preflight
  install_apt_deps    # ensures pick_python() runs so ARCLAP_PYTHON is set
  fetch_release

  # Side-by-side venv at /opt/arclap-station/releases/<version> and swap a symlink.
  local target="${ARCLAP_PREFIX}/releases/$(date +%s)"
  install -d -m 0755 "${target}"
  "${ARCLAP_PYTHON}" -m venv "${target}/venv"
  "${target}/venv/bin/pip" install --upgrade --quiet pip
  local wheel
  wheel="$(arclap_wheel_path)"
  "${target}/venv/bin/pip" install --quiet "${wheel}"

  # Swap the venv robustly whether the current one is a real directory
  # (what install.sh creates) or a symlink. The old `mv -Tf venv.next venv`
  # failed with "cannot overwrite directory" when venv was a real dir, so
  # the update aborted half-done.
  rm -rf "${ARCLAP_PREFIX}/venv"
  mv -T "${target}/venv" "${ARCLAP_PREFIX}/venv"

  # Frontend.
  rm -rf "${ARCLAP_WEBROOT:?}"/*
  tar -xzf "${ARCLAP_TMPDIR}/arclap-station-frontend.tar.gz" -C "${ARCLAP_WEBROOT}"

  # Restart the service. (arclap-uploader.service is intentionally not
  # deployed — the uploader runs in-process — so we must not name it here;
  # `systemctl restart` on a missing unit fails the whole command.)
  systemctl restart arclap-station.service
  ok "Update complete."
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
main() {
  local cmd="${1:-install}"
  case "${cmd}" in
    install)
      preflight
      install_apt_deps
      create_user
      create_layout
      fetch_release
      install_backend
      install_frontend
      install_udev
      install_os_hardening   # NEW: kernel watchdog, swap, journald, SSH, ufw
      install_systemd
      install_caddy
      install_avahi
      enable_services
      print_banner
      install_self
      ;;
    update)
      update_inplace
      ;;
    uninstall)
      uninstall_all "${2:-no}"
      ;;
    *)
      die "Unknown command: ${cmd}. Use install | update | uninstall"
      ;;
  esac
}

main "$@"
