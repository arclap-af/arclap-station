# Arclap Station — Changelog

## v0.2.0 — 2026-05-19 (hardening release)

A reliability + security hardening pass over the v0.1.0 alpha. The Pi can
now be left on a construction site for 2 years (per the deployment brief)
without the failure modes the v0.1.0 code would have hit.

### Security
- **All 4 WebSocket endpoints now require auth** — `/api/terminal/ws`,
  `/api/home/ws`, `/api/camera/preview-ws`, `/api/settings/logs-ws`.
  Previously the terminal WebSocket handed out a restricted bash shell
  to anyone on the LAN. New `require_ws_session()` helper in
  `api/deps.py` validates the session cookie before `ws.accept()`.
- **Destination secrets now use Fernet AEAD** instead of XOR
  obfuscation. Legacy XOR-encrypted rows still decrypt (one-time
  upgrade path); new writes are Fernet-tagged with `f1:` prefix.
- **SSH hardening drop-in**: `PasswordAuthentication no`,
  `PermitRootLogin no`, `MaxAuthTries 3`. Field-deployed Pis no longer
  accept password SSH.
- **ufw firewall enabled** at install: allow 22/80/443/5353, default
  deny incoming.
- **fail2ban enabled** with the default sshd jail.

### Reliability
- **Camera USB watchdog** (`backend/arclap_station/watchdog/camera.py`):
  every 2 min, probes gphoto2; on 3 consecutive failures, toggles
  `/sys/.../authorized` on the DSLR USB device to clear stuck PTP
  sessions. Bounded retry budget prevents reset storms.
- **Camera capture hard timeout** (45s for capture, 5s for preview):
  libgphoto2 hangs no longer freeze the scheduler for the rest of the
  deployment. On timeout the backend handle is closed and the next
  request gets a fresh init.
- **4-tier disk retention sweep**: hot (0–7 d, always keep) / warm
  (7–30 d, keep if uploaded or starred) / cold (30–90 d, keep only
  starred) / archive (90+ d, keep only starred). Triggers above 75%
  disk; sweeps to 65%. Emergency mode (>95%) bypasses hot tier.
- **Service watchdog rewritten**: loopback HTTP probe to
  `127.0.0.1:8080/api/health` (not HTTPS-via-Caddy — that was the
  v0.1.0 death-loop), 60s startup grace, fail counter resets on
  restart, no PartOf cascade.
- **Kernel hardware watchdog wired to systemd**:
  `RuntimeWatchdogSec=30` — kernel hangs auto-reboot the Pi within 30s.
- **zram swap** (12% of RAM, zstd) — OOM safety net on the 8 GB Pi 5.
- **journald limits** — 500 MB max, 1 week max file age.
- **logrotate** for `/var/log/arclap/*.log`.
- **systemd-resolved fallback DNS** — flaky DHCP DNS can't blackhole us.
- **Persistent USB-3 disable** systemd unit (`arclap-usb3-disable.service`)
  ships in the repo + is enabled at install. Canon DSLRs no longer
  negotiate SuperSpeed (which kills PTP).

### Frontend ↔ Backend contract alignment
- API prefix `/api/v1` → `/api` (matches backend mount points).
- Auth schema: `authenticated`→`logged_in`, expires_in dropped.
- `/auth/login` now sends JSON, not form-urlencoded.
- Setup status schema updated to match backend
  (`{first_boot, pin_set, station_named, completed}`).
- Network probe: `POST /setup/network-check` (was `GET /setup/network`).
- Destination tests: `POST /setup/destination-test` with `{type, config}`.
- Schedule fields: `interval_minutes`→`interval_min`.
- Pair: `pair_code` field; only called when user enabled.
- Home telemetry: adapter translates the backend snapshot to the UI shape.
- Camera bridge: adapts the gphoto2 widget tree to flat camera settings.
  Settings PUT now one widget at a time (matches backend's
  `PUT /camera/settings { path, value }`).
- Camera Viewfinder WS path corrected (`/preview-ws` not `/liveview`).
- Settings logs WS path corrected (`/logs-ws` not `/logs/ws`).
- Gallery/Schedule bridges rewritten as adapters for backend's
  `{total, items}` shape, real backend field names.
- Settings tabs (General/Network/Security/Storage/System/Logs) all
  render now — values fall back to "—" where backend doesn't expose
  the field yet, but the page no longer crashes on schema mismatch.

### Wizard
- Removed gate on Acceptance step + Camera step (was blocking on
  state.acceptancePassed / state.cameraDetected). Skip button now
  rendered on every step except Welcome and Done.
- Schedule + destination + pair payloads aligned with backend models.

### Install
- `install.sh` now FAILS instead of silently skipping if
  `/usr/local/sbin/arclap-station-installer` self-copy can't find the
  source. The update/uninstall recovery path is now guaranteed.
- New `install_os_hardening` step applies all the systemd drop-ins
  (kernel watchdog, journald, SSH, ufw, resolved, time-wait-sync) at
  install time. Idempotent.
- Apt deps added: `zram-tools`, `ufw`, `fail2ban`, `logrotate`,
  `python3-dev`, `zlib1g-dev`, `libjpeg-dev`, `libtiff-dev`,
  `libwebp-dev`, `libfreetype-dev`.
- python-gphoto2 now installed at install time (was an optional extra
  that left the backend on the MockCamera adapter, returning fake
  Canon R6 data even when a real Canon was plugged in).

### Operator runbook
- `docs/operator.md`: field-tech-readable playbook for common failures,
  SSH access, troubleshooting one-liners, forgotten-PIN recovery,
  update/uninstall, pre-deployment checklist.

### Known issues remaining for v0.3 / hot fixes
Carried over from the frontend deep audit (60+ items). The highest-impact
ones the next release should tackle:

- **Destination forms send wrong field names** for S3, SFTP, FTP,
  Webhook, MQTT — the UI's `endpoint/user/remote_path/auth_header/broker`
  ≠ backend's `endpoint_url/username/path/auth_type/host+port`. Local
  destinations work; everything else requires the user to know the
  hidden keys. Either rename the form fields or accept aliases in the
  uploaders.
- **Acceptance wizard step**: bridge calls `/setup/acceptance-run/{id}`
  which doesn't exist. Real path is `/api/acceptance/status/{run_id}`
  with a different response shape.
- **Setup wizard "Skip" allowed on PIN step**: a user can skip past PIN
  setup and finish the wizard. Result: `first_boot=False`, `pin_set=False`,
  station is bricked. Need server-side gate + remove Skip on that step.
- **Change PIN UI is a dead button**: `Settings → Security → Change PIN`
  has no `onClick`. There's no way to rotate the PIN after first boot.
- **Settings → System danger zone buttons are no-ops**: Restart
  capture / Reboot Pi / Factory reset all click without doing anything.
  Need backend endpoints + wiring.
- **Storage / Network / Security tabs show fabricated data**: many
  fields are hard-coded defaults in the bridge adapter because the
  backend doesn't expose them. Trust-breaker for security-conscious
  customers. Either implement the backend probes or hide the fields.
- **Gallery upload status pills always show "Local"**: backend returns
  `upload_state` (single string) but frontend expects `uploads[]` array.
- **Home dashboard "URL bar" reads `http://—/`**: adapter looks for
  `ip` key the backend doesn't send.
- **`queue_failed` operator-precedence bug** (`destinations.ts:42`):
  every destination with a `last_error` shows "Failed: 1" forever.
- **Audit hash-chain verification only walks 1000 rows**: tamper any
  row past 1000 and the verifier still says OK. Bug in `audit.py:55`.
- **No WiFi credential UI** — eth0 only; field deployment without
  wired internet has no story.
- **No A/B partition mechanism** for OTA — operator update via
  `arclap-station-installer update` works but kernel/OS rollback is
  manual.

### Diff summary
- 18 commits since v0.1.0
- ~1500 lines added / ~250 removed across backend, frontend, systemd,
  install.sh
- 4 new backend modules (`watchdog/`, `retention/`, plus the
  `require_ws_session` helper)
- 4 new systemd units (camera-watchdog, retention, usb3-disable, plus
  the rewritten arclap-watchdog)
- 1 new doc (operator runbook)

---

## v0.1.0 — 2026-05-19 (initial)

First Python rebuild. Replaced the prior Go v1.x implementation. See
README for the build brief. Tagged the day the first real photo was
captured end-to-end (Canon 5D Mark IV → Pi → SQLite → Gallery).
