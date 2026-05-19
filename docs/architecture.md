# Arclap Station — Architecture

This page expands on the diagram in the [README](../README.md) and explains the
"why" behind each layer.

---

## 1. One-paragraph summary

A fresh Raspberry Pi 5 boots Raspberry Pi OS Bookworm 64-bit. `install.sh`
drops a Python wheel into `/opt/arclap-station/venv`, a static SPA into
`/var/www/arclap`, five systemd units, a Caddy config, a udev rule, and an
Avahi service record. On every boot, systemd brings up `caddy`, `avahi-daemon`,
and the Arclap units in dependency order. Caddy terminates HTTPS on `:443`
with a self-signed `tls internal` cert and proxies to a UNIX socket. Uvicorn
on the other end runs the FastAPI app, which talks to libgphoto2 for camera
control, APScheduler for time-lapse, a worker process for uploads, and SQLite
for everything stateful. The SPA is a Vite/React build that uses Tanstack
Query + Zod for the API surface and a `<video>` plus `<img>` for the MJPEG
viewfinder.

---

## 2. The five units

| Unit | Purpose | Restart | Hardening |
|------|---------|---------|-----------|
| `arclap-station.socket` | Holds the UDS so updates can hand-off without dropping connections. | n/a | `SocketUser=arclap`, `SocketMode=0660` |
| `arclap-station.service` | Uvicorn + FastAPI. | `always` (3s) | strict; `Type=notify`; `WatchdogSec=30s` |
| `arclap-uploader.service` | Worker process that drains `state.db.uploads`. | `always` (5s) | strict |
| `arclap-watchdog.service` | Oneshot health probe. | n/a | minimal — runs as root to be able to `systemctl restart` |
| `arclap-watchdog.timer` | Fires `arclap-watchdog.service` every 30s. | n/a | n/a |

The watchdog talks to Caddy at `https://127.0.0.1/api/health` rather than the
UDS directly so it exercises the full path the LAN sees. 3 consecutive
failures (tracked in `/run/arclap-watchdog.fail`) trigger a restart of the
main service. The kernel watchdog at `/dev/watchdog` is wired through
`RuntimeWatchdogSec=60s` in `arclap-station.service`, and the app emits
`WATCHDOG=1` over `$NOTIFY_SOCKET` every 15s as a heartbeat.

---

## 3. Why a UNIX socket instead of localhost TCP

Three reasons:

1. **No listening TCP port on the device** that the Pi itself can map. Anything
   reaching Arclap must pass through Caddy, which enforces TLS and HSTS.
2. **Filesystem permissions** are simpler to reason about than `lo` ACLs.
3. **Socket activation** lets us hand the listening socket from systemd to
   either Caddy or the service across restarts, so the LAN never sees a
   connection-refused window during an update.

---

## 4. The data plane

- **SQLite** is the canonical store. Three files under `/var/lib/arclap/`:
  `state.db` (the cockpit's working set), `scheduler.db` (APScheduler's job
  store), and `audit.db` (append-only operator actions). All use WAL mode.
- **Photos** live under `/media/sdcard/photos/<YYYY>/<MM>/<DD>/`. We don't put
  them in the SQLite blob store because (a) we'd need to re-stream them out and
  (b) cards are cheap.
- **Thumbnails** are generated on capture and live next to the photo under
  `_thumb.webp`. The cockpit serves them through a path-validated handler.

---

## 5. Why the image only builds in CI

`pi-gen` mounts a loop device, debootstraps Bookworm, runs a chroot, and
exports an `img.xz`. On macOS / Windows hosts this needs Docker-in-Docker plus
nested virtualisation; on a Linux laptop it works but takes 30–60 min and
requires root. We get all of that for free in GitHub Actions Ubuntu runners,
so the `image` target on the Makefile intentionally errors out and points at
the workflow.

---

## 6. Update flow

```
1. arclap-station update
2. install.sh fetches the new wheel + frontend bundle
3. python -m venv /opt/arclap-station/releases/<ts>/venv
4. pip install <new-wheel>
5. ln -sfn releases/<ts>/venv /opt/arclap-station/venv.next
6. mv -Tf venv.next venv     # atomic
7. systemctl restart arclap-station.service
   ↳ systemd hands the existing socket FD to the new Uvicorn
   ↳ Caddy sees nothing change
8. If healthcheck fails for 90s, watchdog triggers + journald has the trace
```

The previous release dir is kept under `/opt/arclap-station/releases/` so an
operator can roll back with one `mv -Tf`.

---

## 7. The wizard's state machine

The wizard's progression is stored in `state.db.wizard_state`:

```
welcome → network → time → identity → camera → exposure → schedule
        → destinations → acceptance → done
```

Each step writes its own row; the final `done` row freezes the wizard. A
support call can re-open the wizard from the Settings page (requires the
operator password) which sets `wizard_state` back to `welcome` without
losing existing destinations.

---

## 8. The MJPEG viewfinder

We expose `/viewfinder.mjpg` as a `multipart/x-mixed-replace` boundary
stream. Each frame is a JPEG that libgphoto2's preview API returns. The
endpoint flushes immediately (no Caddy buffering — see `flush_interval -1`
in the Caddyfile). The cockpit binds an `<img src="/viewfinder.mjpg">`
which the browser handles natively. A WebSocket on `/ws/viewfinder` carries
exposure metadata sidecar to the stream (shutter, aperture, ISO, focus).
