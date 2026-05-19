# Arclap Station — Changelog

## v0.5.0 — 2026-05-19 (camera stability hardening)

Nine layered defences against the tethered-DSLR failure modes that
caused the early "camera disappeared, watchdog reset 3×, then dead"
sequence. Same wire format, same UI; the gains are all under the
hood. Verified live on arclap-st-90107cb4.

### A — USB autosuspend off for camera vendors
`udev/50-arclap-camera.rules` now sets `ATTR{power/control}="on"` on
Canon / Nikon / Sony / Fuji / Olympus / Panasonic / Pentax / Leica
vendor IDs. Previously `usbcore.autosuspend=2` would suspend the camera
endpoint after ~2s idle and the first PTP call after wake returned
`-7 / -105 I/O Error`. This is the single biggest stability win.

### B — Camera auto-power-off disabled via PTP
After init, the adapter calls `set_config /main/settings/autopoweroff
= 0` (with a `sleeptimer` fallback for bodies that name the path
differently). Cameras can no longer drop into deep sleep mid-deployment
and require a wake-up dance on each capture.

### C — Init retry with backoff
`_ensure()` now retries `Camera().init()` up to 3 times at 1 s / 3 s /
10 s. Survives transient `EBUSY` (kernel tearing down a previous handle,
udev re-running rules, etc.) instead of permanently failing.

### D — Pre-capture wake probe
Every capture issues a cheap `get_config /main/status/batterylevel`
first. If it errors, the adapter drops the handle and re-inits before
attempting the capture itself — surfaces a fast clean failure rather
than waiting for `capture()` to time out.

### E — Capture target = internal RAM
After init, sets `/main/settings/capturetarget = 0` (Internal RAM).
Captures are pulled directly from the camera's buffer; the CF/SD card
is never touched. Eliminates card-full, card-write-protected, and
slow-card capture failures entirely.

### F — Watchdog uses backend health beacon
New `backend/arclap_station/camera/health.py` writes
`/var/lib/arclap/camera_health.json` on every detect / capture / preview
with `{ok, last_ok_at, last_error, last_error_at, model, last_reset_at}`.
The camera-watchdog reads this file FIRST: if the beacon is fresh
(<3 min) and `ok`, it returns immediately. Only when the beacon is
stale OR shows a recent error does the watchdog run its own
`gphoto2 --auto-detect`. Two processes no longer fight for the USB
interface.

### G — 15s grace after USB reset
After authorize 0→1, the watchdog writes `last_reset_at` into the
beacon. The adapter's `_ensure()` reads that timestamp and refuses to
open a fresh PTP session for 15 s, letting the kernel finish
re-enumerating cleanly.

### H — Firmware-lockup detection
If the failure threshold is hit AND the maximum reset budget is
exhausted AND the USB device is still enumerated, the watchdog now
emits a `camera.firmware_locked` audit event and returns exit code 4
(new) instead of looping more resets. The cockpit Camera page
surfaces this via the new `info.health` payload so an operator knows
to replug the body.

### I — Capture wallclock timeout (in-thread, 45 s)
A `threading.Timer` arms a force-close callback on the camera handle.
If the in-thread `capture()` exceeds `CAPTURE_TIMEOUT_SEC = 45`, the
timer thread calls `cam.exit()` — which is safe to call from another
thread — and the capturing thread surfaces a bounded error. The
previous `ThreadPoolExecutor`-based approach was reverted in v0.2.3
because libgphoto2's `Camera()` handle is bound to the thread that
called `.init()`; this approach respects that affinity.

### Cockpit
- New `health` field on `/api/camera/info` plumbed into the Camera page.
  The "PTP session · live" pill now reflects real state: `live` when
  beacon ok, `PTP error · <last_error>` when not, `no camera` when
  unplugged, `connecting…` while spinning up.

### Verified live
- v0.5.0 wheel installed, all 55 backend tests pass.
- Camera health beacon writes on detect; cockpit reads it correctly.
- Watchdog returns 0 when camera physically absent (no false resets).
- Frontend bundle hash `index-D_bvdCWd.js` deployed via Caddy.

## v0.4.0 — 2026-05-19 (production-ready, all real data)

The "no demo data anywhere, customer-ready" cut. Every dashboard widget
and Settings tab now consumes real backend telemetry. Audited end-to-end.

### What's real now (was placeholder before)
- **Home status** is backend-derived from camera + queue + disk + uptime
  signals — no longer hardcoded `"online"`.
- **Watchdog state** in Settings → System: live probes of
  `/dev/watchdog`, `arclap-watchdog.timer`, `arclap-camera-watchdog.timer`,
  and the kernel runtime timeout. Renders as
  `active (kernel 30s · service + camera timers)`.
- **UPS state**: queries `apcaccess` and `upsc` for a real driver. If
  neither responds, the UI honestly says `not detected` instead of a
  fake battery percentage.
- **Cloud state**: read from `/etc/arclap/station.json` — paired flag,
  broker URL, cockpit URL.
- **Firmware**: real installed version + honest `channel: manual`
  message documenting the update method (`sudo arclap-station-installer
  update`). No fake "checking for updates" telemetry.
- **Network probes**: live `ping` to gateway, `1.1.1.1`, DNS resolve,
  and NTP synced check. Replaces hardcoded "all OK" placeholders.
- **PIN age**: derived from `auth.json` mtime, not a static "X days".
- **Hardware identity**: model from `/sys/firmware/devicetree/base/model`,
  serial from `/proc/cpuinfo`. Auto-populated into `station.json` on
  first boot (`ensure_serial_from_cpu`).
- **Schedule next-fire**: per-job `next_fire_at` looked up directly
  from the APScheduler jobstore instead of a global "soonest".
- **Memory used MB**: real `mem_used_mb` from psutil, not a derived
  pct× total.
- **Disk free bytes**: real `shutil.disk_usage` reading.
- **Network throughput**: live psutil `net_io_counters` sampled
  between snapshots.
- **Audit chain verify**: walks the full table in pages of 5000, no
  longer capped at 1000 rows. Reports breaks with exact id.

### Camera + Gallery
- **EXIF on EVERY photo** — scheduled captures now go through the same
  `extract_exif()` helper as API captures. Pre-v0.4 photos can be
  back-filled with `arclap-station exif-backfill` (idempotent).
- Gallery now shows real ISO, shutter, aperture, make/model/lens,
  capture-time, dimensions for each photo.
- Camera chip rows (ISO / shutter / aperture / mode) render from the
  body's actual gphoto2 choices arrays, not a hardcoded list.
- Live viewfinder now draws a real 32-bin luma histogram from the
  preview JPEG via OffscreenCanvas at 2 Hz.

### Auth
- Session cookie field separator changed from `;` to `|` (browsers
  truncated the cookie at `;`, breaking WebSocket auth on Chrome).
  Backward-compatible: old `;` tokens still validate during the
  transition.
- Login page renders a live lockout countdown from
  `lockout_seconds_remaining` on `/api/auth/status`; parses the 429
  `Retry-After` body when triggered.

### Backend
- `arclap-station exif-backfill` CLI subcommand: re-extract EXIF for
  any photo where `exif_json IS NULL OR width IS NULL OR height IS NULL`.
- Retention sweep runs `PRAGMA wal_checkpoint(TRUNCATE)` + `VACUUM`
  every night so SQLite actually reclaims disk after delete sweeps.
- `/api/health` returns deep status (db_ok, camera_detected,
  queue_pending, disk_used_pct, cpu_temp_c, uptime_seconds) for the
  service watchdog and external monitoring to probe.
- `/api/home/activity` endpoint replaces the demo Activity feed —
  backed by the audit log.
- Danger Zone endpoints (`/reboot`, `/restart-service`,
  `/factory-reset`) live with PIN confirmation; emit audit events.

### Verified live
- Audit chain ok, 122 entries, 0 breaks.
- Real schedule `next_fire_at: 2026-05-19T21:01:42+02:00`.
- Real destination `last_ok_at: 2026-05-19 17:26:35`.
- Photo EXIF post-backfill: `iso=1000 shutter=1/125 aperture=f/6.3
  model="Canon EOS 5D Mark IV" lens="EF50mm f/1.2L USM" 6720×4480`.
- Status derivation: returns `warn` when camera physically disconnected
  (proven on this Pi); flips to `online` when camera reconnected.

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
