# Arclap Station — Operator Runbook

Field-tech-readable. If you're standing in front of a station that isn't
behaving, start here.

---

## 0. Identify the station

Every station has a name like `arclap-st-90107cb4`. The 8-char hex suffix is
the last 8 digits of the Pi 5's CPU serial. It's printed on the asset label
and shown on the cockpit's top-left mark.

URL: `https://arclap-st-<serial>.local/` (from any laptop/phone on the
same LAN). If `.local` doesn't resolve from your device, use the IP
(printed on the cockpit's URL bar after you log in once, or get it from
the site router's DHCP table).

PIN: 6 digits. Set during initial commissioning. Forgotten PIN recovery
is in §6 below.

---

## 1. Quick health check (60 seconds)

From the cockpit:
1. Open the URL. Login screen → enter PIN.
2. **Home** tab: top-right pill should be green ("Live"). Captures-today
   should be a sensible number. CPU < 60%, temp < 70°C, storage < 80%.
3. **Camera** tab: top-right "PTP session · live" pill should be green.
   The viewfinder should show a live feed within 5 seconds.
4. **Gallery** tab: most-recent photo timestamp should be within
   the schedule's interval. (e.g. 5-min schedule → newest photo < 6 min old)

If all four are green, the station is healthy. Stop reading.

---

## 2. SSH access (when the cockpit can't help)

```
ssh pi01@arclap-st-<serial>.local
# or use the IP if mDNS doesn't work
```

You'll need the Pi's `pi01` password (default per-station, written on the
asset label). SSH password auth is disabled by default in v0.2+ — you must
have a key in `~pi01/.ssh/authorized_keys` set up at warehouse-flash time.

Once in, become root for everything else:

```
sudo -i
```

---

## 3. Common failures

### "Camera offline" / no captures landing

```bash
# Is gphoto2 seeing the camera at the OS level?
gphoto2 --auto-detect
# Expected: "Canon EOS 5D Mark IV   usb:002,xxx"

# If usb:005,* (USB 3 SuperSpeed) — the USB-3 disable service didn't run:
sudo systemctl status arclap-usb3-disable.service
sudo systemctl restart arclap-usb3-disable.service
# Unplug + replug the camera. It should now enumerate on bus 2 or 4.

# If autodetect shows the camera but capture fails with "PTP I/O Error":
sudo /opt/arclap-station/venv/bin/arclap-station camera-watchdog
# That runs the same logic the timer fires every 2 min. It probes and
# auto-resets the USB if needed.

# Last resort:
sudo systemctl restart arclap-station
```

### "Uploads stuck" / queue not draining

```bash
# Look at queue stats:
sudo sqlite3 /var/lib/arclap/state.db "SELECT state, COUNT(*) FROM upload_queue GROUP BY state;"

# Anything in 'failed' or 'failed_permanent' state?
sudo sqlite3 /var/lib/arclap/state.db "SELECT id, dest_id, attempts, last_error FROM upload_queue WHERE state IN ('failed','failed_permanent') LIMIT 5;"

# Most common cause: the destination's network is broken. Verify the
# destination from the cockpit (Destinations tab → click the destination
# → "Test" button).

# Drain anything that's due:
sudo /opt/arclap-station/venv/bin/python -c "from arclap_station.uploaders.queue import get_queue; print(get_queue().drain_once(), 'items processed')"
```

### Disk full

```bash
df -h /media/sdcard/photos
# If > 90%, captures will silently pause (skip rule in scheduler/rules.py).

# Force a retention sweep right now:
sudo /opt/arclap-station/venv/bin/arclap-station retention-sweep

# Retention auto-runs every night at 03:15 local time. To see the schedule:
systemctl list-timers --all | grep arclap-retention
```

### Service crashed / cockpit unreachable

```bash
sudo systemctl status arclap-station
sudo journalctl -u arclap-station -n 50

# Restart:
sudo systemctl restart arclap-station

# Caddy down too?
sudo systemctl restart caddy
```

### Pi unreachable on the network

If you can ping the IP but no `.local` resolution:
```bash
sudo systemctl restart avahi-daemon
```

If the Pi is fully offline: physically unplug power, wait 10 seconds, plug
back in. The hardware watchdog auto-reboots on kernel hang within ~30s.

---

## 4. Where everything lives

| What | Path |
|---|---|
| Backend code | `/opt/arclap-station/venv/lib/python3.X/site-packages/arclap_station/` |
| Photos | `/media/sdcard/photos/YYYY/MM/DD/` |
| State DB (SQLite WAL) | `/var/lib/arclap/state.db` |
| Scheduler DB | `/var/lib/arclap/scheduler.db` |
| Auth (PIN bcrypt hash) | `/etc/arclap/auth.json` |
| Destination secrets | `/etc/arclap/destinations/` + encrypted in state.db |
| Station identity | `/etc/arclap/station.json` |
| Audit log | `/var/lib/arclap/state.db` table `audit_log` |
| Caddy config | `/etc/caddy/Caddyfile` (generated from template at install) |
| Installer | `/usr/local/sbin/arclap-station-installer` |
| Camera watchdog state | `/var/lib/arclap/camera_watchdog.json` |
| Service unit | `/etc/systemd/system/arclap-station.service` |

---

## 5. Useful one-liners

```bash
# Live tail of backend logs
sudo journalctl -fu arclap-station

# Last 10 captures
sudo sqlite3 /var/lib/arclap/state.db "SELECT id, filename, captured_at, upload_state FROM photos ORDER BY id DESC LIMIT 10;"

# Audit log of the last hour
sudo sqlite3 /var/lib/arclap/state.db "SELECT ts, actor, event FROM audit_log WHERE ts > datetime('now','-1 hour') ORDER BY ts DESC;"

# Force a capture right now
curl -sk --resolve arclap-st-$(awk -F: '/^Serial/{gsub(/ /,"",$2);v=tolower($2);print substr(v,length(v)-7)}' /proc/cpuinfo).local:443:127.0.0.1 \
  -c /tmp/c.txt -X POST -H "Content-Type: application/json" \
  -d '{"pin":"<your-PIN>"}' https://arclap-st-*.local/api/auth/login >/dev/null && \
curl -sk -b /tmp/c.txt -X POST https://arclap-st-*.local/api/camera/capture

# Show disk usage
df -h /media/sdcard/photos /

# Show systemd timers
systemctl list-timers --all | grep arclap

# Check the kernel hardware watchdog is active
sudo cat /sys/class/watchdog/watchdog0/state 2>/dev/null
sudo systemctl show -p RuntimeWatchdogSec
```

---

## 6. Forgotten PIN recovery

Only works with SSH access to the Pi. There is no password-reset over the
network — by design, otherwise an attacker who gets onto the LAN could
reset the PIN.

```bash
sudo /opt/arclap-station/venv/bin/python3 -c "
from arclap_station.auth import AuthManager
a = AuthManager()
a.set_pin('246810')   # set to whatever you want; 4-12 digits
print('PIN reset:', a.is_pin_set())
"
sudo chown arclap:arclap /etc/arclap/auth.json
```

---

## 7. Update the station

```bash
sudo /usr/local/sbin/arclap-station-installer update
```

This pulls the latest release wheel + frontend bundle and installs into a
side-by-side venv at `/opt/arclap-station/releases/<ts>`. On success it
swaps the `venv` symlink and restarts the service. On failure the old
version stays running.

State (PIN, destinations, captured photos) is **not touched** by an update.

---

## 8. Uninstall / factory reset

```bash
# Stop services and remove everything except photos + persistent state:
sudo /usr/local/sbin/arclap-station-installer uninstall

# Nuke EVERYTHING including photos and audit log:
sudo /usr/local/sbin/arclap-station-installer uninstall --purge
```

---

## 9. Pre-deployment checklist

Before driving to the site:

- [ ] Pi 5 RTC battery (CR2032) fitted — verify with `hwclock --show` from a powered Pi with NO network connection. If it returns `1969-12-31` (or similar epoch garbage), the battery is dead → replace.
- [ ] Pi 5 power supply is the **official 5 A** unit. The amber LED + "this power supply is not capable of 5A" warning at boot means USB peripherals will be throttled — DSLRs will eventually drop the PTP session.
- [ ] SD card is at least 64 GB, A1 or A2 application class.
- [ ] Camera cable is the original Canon-supplied cable (or known-good data USB cable). Generic charge-only cables WILL fail PTP intermittently.
- [ ] Initial install ran end-to-end (success banner printed).
- [ ] First photo captured via cockpit Shutter button.
- [ ] Destination configured + test passed.
- [ ] Schedule configured + first scheduled capture observed.
- [ ] PIN written down somewhere outside the station.

---

## 10. When all else fails

SSH in, run:

```bash
sudo /opt/arclap-station/venv/bin/arclap-station healthcheck
sudo /opt/arclap-station/venv/bin/arclap-station version
sudo journalctl -u arclap-station --since "1 hour ago"
sudo journalctl -u caddy --since "1 hour ago"
sudo journalctl -u arclap-camera-watchdog --since "1 hour ago"
df -h
free -h
systemctl list-units --failed
```

Send that output (plus a short description of the symptom) to support.
