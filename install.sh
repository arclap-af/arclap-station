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

  # Use the distro-default python3 — works across Pi-OS Bookworm
  # (3.11), Ubuntu Noble (3.12), and the post-Noble releases (3.12 /
  # 3.13). All three are wire-compatible with the codebase.
  local packages=(
    libgphoto2-dev
    gphoto2
    caddy
    avahi-daemon
    avahi-utils
    python3
    python3-venv
    python3-pip
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

  # Resolve the python3 to actually use. Distro default is 3.11+ on
  # every supported target (Pi-OS Bookworm, Ubuntu Noble/Oracular/
  # Plucky/Resolute), but we double-check the version so we fail loudly
  # if someone runs this on an EOL release.
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

  # Group membership: plugdev (USB), video (V4L), dialout (serial cameras).
  for g in plugdev video dialout; do
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

  if [[ ! -x "${venv}/bin/${ARCLAP_PYTHON}" ]] && [[ ! -x "${venv}/bin/python" ]]; then
    "${ARCLAP_PYTHON}" -m venv "${venv}"
    ok "Created venv at ${venv}"
  else
    ok "venv already exists"
  fi

  "${venv}/bin/pip" install --upgrade --quiet pip setuptools wheel

  # Install the wheel (PEP 427 filename preserved by fetch_release()).
  # --force-reinstall lets re-runs upgrade in place.
  local wheel
  wheel="$(arclap_wheel_path)"
  "${venv}/bin/pip" install --upgrade --force-reinstall --quiet "${wheel}"
  ok "Installed $(basename "${wheel}") into venv"

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

  local script_dir
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  local rule_src="${script_dir}/udev/50-arclap-camera.rules"
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

  local script_dir
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  local src="${script_dir}/systemd"

  if [[ ! -d "${src}" ]]; then
    die "Cannot find systemd/ alongside install.sh. Re-clone the repo or download the release tarball."
  fi

  install -m 0644 "${src}/arclap-station.service"  /etc/systemd/system/arclap-station.service
  install -m 0644 "${src}/arclap-station.socket"   /etc/systemd/system/arclap-station.socket
  install -m 0644 "${src}/arclap-uploader.service" /etc/systemd/system/arclap-uploader.service
  install -m 0644 "${src}/arclap-watchdog.service" /etc/systemd/system/arclap-watchdog.service
  install -m 0644 "${src}/arclap-watchdog.timer"   /etc/systemd/system/arclap-watchdog.timer

  systemctl daemon-reload
  ok "systemd units installed"
}

# ---------------------------------------------------------------------------
# 10. Caddyfile
# ---------------------------------------------------------------------------
install_caddy() {
  step 10 "Configuring Caddy reverse proxy"

  local script_dir
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  local serial
  serial="$(short_serial)"
  local tmpl="${script_dir}/caddy/Caddyfile.template"

  if [[ ! -f "${tmpl}" ]]; then
    die "Missing caddy/Caddyfile.template alongside install.sh"
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

  local script_dir
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  local serial
  serial="$(short_serial)"
  local src="${script_dir}/avahi/arclap-station.service"

  if [[ ! -f "${src}" ]]; then
    die "Missing avahi/arclap-station.service alongside install.sh"
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

  systemctl enable --now caddy
  systemctl enable --now avahi-daemon
  systemctl enable --now arclap-station.socket
  systemctl enable --now arclap-station.service
  systemctl enable --now arclap-uploader.service
  systemctl enable --now arclap-watchdog.timer

  sleep 5
  if ! systemctl is-active --quiet arclap-station.service; then
    warn "arclap-station.service is not active. Recent logs:"
    journalctl -u arclap-station -n 30 --no-pager || true
    die "Service failed to start. See logs above and consult docs/troubleshooting.md."
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

  # Copy this very script to a stable location so `sudo arclap-station uninstall`
  # / `update` (which call back into install.sh) work after the wheel rotates.
  install -m 0755 "$0" /usr/local/sbin/arclap-station-installer
  ok "Installer self-copied to /usr/local/sbin/arclap-station-installer"
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
  rm -rf "${ARCLAP_PREFIX}" "${ARCLAP_WEBROOT}" "${ARCLAP_CONFDIR}" "${ARCLAP_LOGDIR}"
  if [[ "${purge}" == "--purge" ]]; then
    rm -rf "${ARCLAP_HOME}" "${ARCLAP_PHOTODIR}"
  fi

  systemctl daemon-reload
  udevadm control --reload-rules

  ok "Arclap Station removed."
  if [[ "${purge}" != "--purge" ]]; then
    info "State preserved under ${ARCLAP_HOME} and ${ARCLAP_PHOTODIR}. Re-run with --purge to delete."
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

  ln -sfn "${target}/venv" "${ARCLAP_PREFIX}/venv.next"
  mv -Tf "${ARCLAP_PREFIX}/venv.next" "${ARCLAP_PREFIX}/venv"

  # Frontend.
  rm -rf "${ARCLAP_WEBROOT:?}"/*
  tar -xzf "${ARCLAP_TMPDIR}/arclap-station-frontend.tar.gz" -C "${ARCLAP_WEBROOT}"

  # Socket-activated restart preserves listening sockets.
  systemctl restart arclap-station.service arclap-uploader.service
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
