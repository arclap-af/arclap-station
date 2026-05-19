# Arclap Station — journalctl Cheat Sheet

`journalctl` is the canonical source of truth for everything Arclap does at
runtime. The log files under `/var/log/arclap/` are rotated copies for forensics.

---

## Live tails

```bash
# Everything Arclap.
sudo journalctl -fu 'arclap-*'

# Single service.
sudo journalctl -fu arclap-station
sudo journalctl -fu arclap-uploader
sudo journalctl -fu arclap-watchdog
sudo journalctl -fu caddy
sudo journalctl -fu avahi-daemon
```

`-f` follows. Add `-n 200` to start with the last 200 lines for context.

---

## Time windows

```bash
sudo journalctl -u arclap-station --since "5 minutes ago"
sudo journalctl -u arclap-station --since "today"
sudo journalctl -u arclap-station --since "2026-05-19 14:00" --until "2026-05-19 14:30"
sudo journalctl -u arclap-station --since yesterday --until today
```

---

## Boot-relative

```bash
sudo journalctl -u arclap-station -b           # this boot
sudo journalctl -u arclap-station -b -1        # last boot
sudo journalctl -u arclap-station -b -2        # the boot before that
sudo journalctl --list-boots                   # all known boots
```

After a crash, `-b -1` is your friend.

---

## Severity filters

```bash
sudo journalctl -u arclap-station -p err           # ≥ ERR
sudo journalctl -u arclap-station -p warning       # ≥ WARNING
sudo journalctl -u arclap-station -p info..err     # INFO to ERR inclusive
```

Priority numbers: 0 emerg, 1 alert, 2 crit, 3 err, 4 warning, 5 notice, 6 info,
7 debug.

---

## Output formats

```bash
sudo journalctl -u arclap-station -o short-iso   # ISO timestamps
sudo journalctl -u arclap-station -o json        # one JSON object per line
sudo journalctl -u arclap-station -o cat         # just the message, no timestamp
sudo journalctl -u arclap-station -o verbose     # all metadata fields
```

`-o json | jq` is the easiest path to ad-hoc analytics:

```bash
sudo journalctl -u arclap-station -o json --since "1 hour ago" \
  | jq -r 'select(.PRIORITY|tonumber < 4) | "\(.__REALTIME_TIMESTAMP) \(.MESSAGE)"'
```

---

## Common diagnostic recipes

```bash
# What rate is the API serving?
sudo journalctl -u arclap-station --since "5 minutes ago" \
  | grep -c 'request_started'

# Which destinations are erroring?
sudo journalctl -u arclap-uploader -o json --since today \
  | jq -r 'select(.MESSAGE|test("upload_failed")) | .DESTINATION'

# Did the watchdog restart anything?
sudo journalctl -u arclap-watchdog --since today | grep -i restart

# Did Caddy do any cert ops?
sudo journalctl -u caddy --since today | grep -iE 'cert|tls'

# Was the camera disconnected at any point?
sudo journalctl -u arclap-station --since today | grep -iE 'camera_disconnected|usb_unplug'
```

---

## Disk usage + retention

```bash
# How much disk does journald hold?
sudo journalctl --disk-usage

# Vacuum oldest first.
sudo journalctl --vacuum-time=14d
sudo journalctl --vacuum-size=500M

# Permanent config — drop into /etc/systemd/journald.conf.d/arclap.conf:
# [Journal]
# SystemMaxUse=500M
# SystemMaxFileSize=50M
# MaxRetentionSec=30days
```

The installer doesn't change journald's defaults — modify them if you need to.

---

## Export for a bug report

```bash
sudo journalctl -u 'arclap-*' -u caddy -u avahi-daemon \
  --since "1 hour ago" --no-pager > /tmp/arclap-debug.log
tar czf /tmp/arclap-debug.tar.gz \
  /tmp/arclap-debug.log /etc/arclap /var/lib/arclap/audit.db
# Attach /tmp/arclap-debug.tar.gz to your support ticket.
```

`audit.db` includes ASCII-safe operator actions only — no secrets.
