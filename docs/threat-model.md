# Arclap Station — Threat Model

This document captures what we defend against, what we don't, and where the
fault lines are.

---

## 1. Assets

| Asset | Sensitivity | Location |
|-------|-------------|----------|
| Operator credentials (bcrypt hash) | High | `/etc/arclap/auth.json` (mode 0640, owner `arclap`) |
| Destination credentials (S3 keys, SFTP private keys, MQTT passwords) | High | `/etc/arclap/destinations/*.json`; AES-256-GCM at rest, key in the kernel keyring via `python-keyring` |
| Captured photos | Medium | `/media/sdcard/photos/` |
| Audit log | High (integrity) | `/var/lib/arclap/audit.db`; append-only at the app layer; hash chain per row |
| Device key (for signed acceptance reports) | High | `/etc/arclap/device.key`; mode 0600; root-owned |
| TLS cert + key (Caddy `tls internal`) | Medium | `/var/lib/caddy/`; managed by Caddy |
| App binary | Medium (integrity) | `/opt/arclap-station/`; immutable at runtime via `ProtectSystem=strict` + `ReadOnlyPaths` |

---

## 2. Adversaries

| Adversary | Capability | In scope? |
|-----------|------------|-----------|
| **LAN attacker** | Sees Wi-Fi traffic, can ARP-poison, can scan ports. | Yes |
| **Curious operator** | Has the cockpit password, wants to escalate to root. | Yes |
| **Malicious USB device** | A "DSLR" that's actually a USB attack platform. | Partial — we accept the risk for now |
| **Physical attacker with the SD card in hand** | Can mount the card on another machine. | Partial — we don't full-disk-encrypt the SD card |
| **Anthropic-grade nation-state** | Side channels, supply chain. | Out of scope |
| **Cloud breach (S3 / SFTP / MQTT)** | Steals our outbound credentials. | Yes — we rotate per destination |

---

## 3. Mitigations

### Network
- **TLS-only ingress**: Caddy listens on `:443` with `tls internal`. Port 80
  redirects to 443. No plaintext API surface.
- **HSTS** pinned for one year (`max-age=31536000; includeSubDomains`).
- **CSP** blocks `eval`, inline scripts, plugins, and frame embedding.
- **No remote shell**: SSH is enabled by default in the Pi OS image but
  doesn't open inbound on the WAN. The Pi is expected to live behind a router.

### Process
- **Systemd hardening** on every Arclap unit: `NoNewPrivileges=true`,
  `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`,
  `LockPersonality=true`, empty `CapabilityBoundingSet`, narrow
  `ReadWritePaths`, and a `@system-service` syscall filter.
- **Dedicated user** (`arclap`, system UID, no shell, no home content).
- **Restricted PTY** in the terminal tab: a fixed allowlist of commands
  (`gphoto2 --auto-detect`, `lsusb`, `ip a`, `df -h`, `journalctl -u arclap-*`
  capped at 1000 lines), no shell metacharacters.

### Data
- **bcrypt** for the operator password (cost 12).
- **AES-256-GCM** for destination secrets; key in the kernel keyring.
- **Audit log hash chain**: each row stores `sha256(prev_row_hash || canonical_json(row))`.
  Tampering is detectable.
- **Signed acceptance reports**: the device key signs the final wizard
  acceptance JSON. A QR code on the printed report lets a remote reviewer
  verify offline against the device's public key.

### Update
- **Atomic swap** via `mv -Tf` on the venv symlink. Either the old or the new
  is live; never a half-installed state.
- **Watchdog** restarts on 3 consecutive `/api/health` failures.
- **Release artifacts are hashed** (`SHA256SUMS` in every GitHub release). The
  installer doesn't verify them today — see "Known gaps" below.

---

## 4. Known gaps

We're not pretending these don't exist.

- **No verified boot**. An attacker with the SD card in hand can mount it,
  replace the wheel, and re-insert it. We rely on physical control of the
  device for now. Mode B (a future provisioning mode) will fold in a TPM-backed
  measured boot when the hardware lands.
- **No signed wheel verification in the installer**. `install.sh` trusts
  HTTPS to GitHub. We plan to sign wheels with Sigstore once the release
  workflow stabilises.
- **The kernel keyring is process-scoped**. If `arclap-station.service`
  restarts, the AES key is re-derived from a per-device seed, but if the SD
  card is moved to another Pi, the seed is gone and destinations re-prompt for
  credentials. This is by design.
- **`Type=notify` requires the app to send `READY=1`**. If the app forgets, the
  service appears hung and the watchdog will hammer it. We tolerate this in
  exchange for the ordering guarantees notify gives us.
- **Caddy admin API binds to localhost:2019**. Anybody with shell access can
  reconfigure Caddy. The shell access path is the bigger problem; we treat
  Caddy as inside the trust boundary.
- **No anti-tamper on `/media/sdcard/photos`**. An operator with shell access
  can `rm -rf` the photo tree. We log file deletions through inotify into the
  audit log, but we don't prevent them.

---

## 5. Incident response

If you suspect a station is compromised:

```bash
# 1. Capture the audit log + state before doing anything else.
sudo tar czf /tmp/forensic-$(hostname).tar.gz /var/lib/arclap /etc/arclap

# 2. Stop everything.
sudo systemctl stop 'arclap-*'

# 3. Pull the SD card. Image it offline (`dd if=/dev/mmcblk0 of=image.img bs=4M status=progress`).

# 4. Burn-and-rebuild: re-flash a new SD card with the latest image. Don't
#    trust the original card's filesystem at all.

# 5. Rotate every destination credential the station knew about.
```

Email `security@arclap.ch` with the forensic tarball.
