# Arclap Station — First-Boot Wizard Walkthrough

A step-by-step annotated walkthrough of the wizard the operator sees on first
boot. Screenshot paths are placeholders that the CI pipeline (Playwright
happy-path) will fill in.

---

## Step 0 — Browser opens `https://arclap-st-<serial>.local/`

The browser warns about the self-signed certificate. Operator clicks
"Advanced → Proceed anyway". HSTS pins the cert for a year afterwards.

![Cert warning](screenshots/00-cert-warning.png)

---

## Step 1 — Welcome

![Welcome](screenshots/01-welcome.png)

What the operator sees:

- Hostname (`arclap-st-1a2b3c4d`).
- Firmware version + git SHA.
- Hardware probe results: Pi 5, 8 GB RAM, Bookworm aarch64.
- "Begin setup" button → step 2.

What the backend does:

- Locks `wizard_state = 'welcome'` in `state.db`.
- Emits `audit_log.wizard_started` with the requesting IP.

---

## Step 2 — Network

![Network](screenshots/02-network.png)

Options:

- Scan SSIDs (lists every visible SSID with RSSI).
- Manual entry for hidden networks.
- "Use Ethernet" if the operator already has a wired link.

Behind the scenes we call `nmcli` to add a NetworkManager connection profile
named `arclap-wifi-<ssid>`. The connection is auto-activated and the wizard
waits up to 30s for an IPv4.

---

## Step 3 — Time zone & NTP

![Time zone](screenshots/03-timezone.png)

- Picker for the IANA time zone (defaults to the one geocoded from the public
  IPv4 if internet is available).
- Optional custom NTP server.
- The wizard refuses to advance until `timedatectl status` reports
  `System clock synchronized: yes`.

---

## Step 4 — Identity

![Identity](screenshots/04-identity.png)

- Operator chooses a username + password.
- Password rules: ≥ 12 characters, ≥ 1 digit, ≥ 1 symbol.
- bcrypt hashed locally (cost 12); written to `/etc/arclap/auth.json`.
- The wizard issues a one-shot recovery code (shown once; the operator copies
  it). A bcrypt hash of the code is stored alongside.

---

## Step 5 — Camera

![Camera](screenshots/05-camera.png)

- Auto-detection runs `gphoto2 --auto-detect`.
- Operator picks one body from the list (often there's only one).
- Wizard locks the body identifier into `state.db.camera`.
- A test capture is fired; the resulting thumbnail appears.

If detection fails, the wizard surfaces a button to "Try again" plus a
deep-link to the [troubleshooting page](troubleshooting.md).

---

## Step 6 — Exposure baseline

![Exposure](screenshots/06-exposure.png)

- Shutter, aperture, ISO, white balance — set via `gphoto2 --set-config`.
- Live MJPEG viewfinder updates as the operator twiddles.
- "Save defaults" persists these as the schedule's starting values.

---

## Step 7 — Schedule

![Schedule](screenshots/07-schedule.png)

Three modes:

- **Single shot** — manual trigger only.
- **Interval** — every N seconds/minutes/hours, optional time window.
- **Cron** — full cron string for the power user.

The schedule is written to `scheduler.db` and APScheduler picks it up
immediately.

---

## Step 8 — Destinations

![Destinations](screenshots/08-destinations.png)

The operator adds one or more uploaders. Each destination type has its own
form with validation:

| Type | Fields |
|------|--------|
| `s3` | endpoint, region, bucket, prefix, access key, secret key |
| `sftp` | host, port, user, password OR private key, path |
| `ftp` | host, port, user, password, path |
| `https-webhook` | URL, secret, signature header name |
| `mqtt` | broker URL, topic, username, password, TLS toggle |
| `local` | path (must be writable by `arclap`) |

Secrets are stored under `/etc/arclap/destinations/<id>.json` and the secret
field is replaced with a kernel keyring reference.

A "Test now" button runs an authenticated round-trip against each destination
and shows the result inline.

---

## Step 9 — Acceptance

![Acceptance](screenshots/09-acceptance.png)

The wizard fires a final capture, runs every destination's round-trip, and
collects:

- Hardware fingerprint (Pi serial, USB tree, RAM, kernel version).
- App version + git SHA.
- All destination round-trip results (succeeded / failed / latency).
- Operator name + timestamp.
- A signature using `/etc/arclap/device.key`.

The result is a JSON blob the operator can download, plus a QR code that
encodes the signature for offline verification.

---

## Step 10 — Finish

![Cockpit](screenshots/10-cockpit.png)

Wizard sets `wizard_state = 'done'` and redirects to the live cockpit. The
home screen shows:

- Camera status (connected / battery / temperature).
- Last capture + thumbnail.
- Upload queue depth per destination.
- Telemetry sparklines (CPU / RAM / disk / temp).
- A "Re-open wizard" link (auth-gated) for support sessions.
