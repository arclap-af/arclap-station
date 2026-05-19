# Arclap Station — Troubleshooting

The README has the short version. This is the long version, organised by
symptom.

---

## "Browser can't reach the station"

```bash
# From the laptop:
ping arclap-st-<serial>.local
avahi-browse -prt _arclap._tcp
# From the Pi:
hostname
hostnamectl --static
sudo systemctl status avahi-daemon
sudo journalctl -u avahi-daemon -n 30
```

Common causes:

- Laptop on a different VLAN. mDNS does not cross subnets without an mDNS
  relay (the kind enterprise networks deploy with `avahi-daemon --reflector`).
  Use the IPv4 fallback printed by the installer.
- Pi's hostname was reset by another tool. Re-run
  `sudo hostnamectl set-hostname arclap-st-<serial>`.
- avahi-daemon is disabled by default on a fresh Bookworm image — the
  installer enables it, but if you skipped step 11 of install.sh manually
  you'll need to `sudo systemctl enable --now avahi-daemon`.

---

## "TLS certificate warning"

Expected on first boot. Caddy issues a self-signed cert via `tls internal`.
Click through once. After that the browser caches the cert and HSTS pins the
hostname.

If you want to swap in a Let's Encrypt cert for an on-prem station with a real
DNS name pointing at the Pi:

```caddy
arclap-st-1a2b3c4d.example.com {
  tls ops@arclap.ch     # ACME challenge over port 80
  import arclap_common
}
```

(Drop into `/etc/caddy/Caddyfile`, `sudo systemctl reload caddy`.)

---

## "Camera shows in lsusb but never in the cockpit"

The detection chain is:

```
USB hotplug → udev (50-arclap-camera.rules) → /dev/bus/usb/* perms → gphoto2 → arclap-station
```

Each link can fail.

```bash
# 1. udev applied?
sudo udevadm info -q all -n /dev/bus/usb/001/002 | grep -iE 'group|mode'
# Expected: GROUP="plugdev", MODE="0660"

# 2. arclap can see it?
sudo -u arclap gphoto2 --auto-detect

# 3. arclap-station seeing it?
sudo journalctl -u arclap-station | grep -i camera | tail -n 30
```

If gphoto2 hangs or returns "Could not claim the USB device":

```bash
# Something else has the camera. Common culprit:
ps aux | grep -E 'gvfs|gphoto2-volume-monitor|gnome-shell'

# Mask the systemd unit:
sudo systemctl mask gvfs-gphoto2-volume-monitor.service
sudo systemctl restart arclap-station.service
```

Pi 5 with multiple USB devices: the USB 3 bus can starve under heavy load.
Move the camera to the USB 2 port and high-throughput peripherals (SSD) to
USB 3.

---

## "Capture works but every photo upload fails"

```bash
# What does the queue look like?
sudo -u arclap sqlite3 /var/lib/arclap/state.db \
  'select id, destination, status, retries, last_error from uploads order by id desc limit 20;'

# What does each destination say?
ls -la /etc/arclap/destinations/
sudo journalctl -u arclap-uploader -n 50

# Manual smoke test (replace with your real values):
aws s3 cp /etc/hostname s3://your-bucket/test-$(date +%s) --profile arclap
```

The uploader's retry/backoff is per-destination, so one wrong S3 key won't
block the SFTP destination. Failed uploads stay in the queue with
`status='dlq'` after 5 retries; you can re-queue them from the Destinations
panel in the cockpit.

---

## "The cockpit is unresponsive after a long timelapse"

The watchdog should have already restarted it. Check:

```bash
sudo journalctl -u arclap-watchdog -n 50
ls -la /run/arclap-watchdog.fail   # if present, the watchdog is counting
cat /run/arclap-watchdog.fail      # how many failures
```

If you find an unusually long `journalctl -u arclap-station` stuck on a single
log line, capture it for a bug report and `sudo systemctl restart arclap-station`.

---

## "I broke it — how do I roll back?"

```bash
ls /opt/arclap-station/releases/
# Each subdirectory is a prior install.
sudo ln -sfn /opt/arclap-station/releases/<older-ts>/venv \
            /opt/arclap-station/venv.next
sudo mv -Tf /opt/arclap-station/venv.next /opt/arclap-station/venv
sudo systemctl restart arclap-station.service
```

If the wheel was somehow corrupted in place, re-run `sudo arclap-station update`
with the previous `ARCLAP_VERSION=` pinned.

---

## "Network is fine but the wizard step 'Time zone & NTP' fails"

`ntpsec` may not be running, or the chosen NTP server is unreachable.

```bash
sudo systemctl status ntpsec
sudo ntpq -p
# Try a public pool:
sudo ntpdate -q pool.ntp.org
```

If the Pi has no internet during install, set the system clock manually
(`sudo timedatectl set-time '2026-05-19 12:00:00'`) so HTTPS handshakes don't
fail on the certificate `notBefore` check, then re-run NTP later.

---

## "I see `Type=notify` watchdog killing the app"

The Python app must send `WATCHDOG=1` to `$NOTIFY_SOCKET` periodically. If
you're running under `--reload` for development the reloader fork sometimes
doesn't pick up `$NOTIFY_SOCKET`. Two workarounds:

1. Set `WatchdogSec=0` temporarily in
   `/etc/systemd/system/arclap-station.service.d/override.conf`.
2. Or run `arclap-station serve --no-systemd-notify` for the dev session.

Production never uses `--reload`.
