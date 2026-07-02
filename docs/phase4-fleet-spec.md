# Phase 4 — Fleet-grade: from one great station to 100 you can trust

**Status:** specification (no code yet). **Owner:** Ali. **Audience:** Ali + Tallium.
**Prereqs:** hardware (below), an AWS environment (eu-central-1), and ≥1 second Pi to test against.

This is the plan to take Arclap Station from "excellent on one Pi" to "operable
as a fleet." Everything in Phases 0–3 (shipped, v0.9.5–v0.9.9) made a *single*
station correct, safe, and self-healing. Phase 4 is about **seeing, updating,
and trusting stations at scale without driving to site.**

It's deliberately **not** written as code because it can't be finished from a
laptop: it needs devices, a cloud account, and field validation. What follows
is concrete enough to execute against directly.

---

## 0. What already exists (don't rebuild these)

The single-station work left real seams to build on:

- **Heartbeat + alert transport** — `health/alerts.py::send_heartbeat` already
  POSTs `{type, overall, score, version, station, serial, site, captures_today,
  queue_pending}` on an interval, and `evaluate_and_alert` POSTs on any
  health transition. Today these go to a per-station webhook. *The fleet
  dashboard is mostly a place to point them.*
- **Fleet station-card** — `api/system.py::/api/system/info` already returns
  the per-station summary a dashboard row needs.
- **Update-check** — `/api/system/update/check` already compares the running
  version against GitHub release tags.
- **A/B OTA design** — Confluence §12.5.4 already specifies A/B partitions as
  non-negotiable. This spec makes it real.
- **Audit export** — `audit.py::export_signed` already produces a hash-chained,
  Ed25519-signable log bundle — the per-station compliance artifact.

---

## 1. Fleet dashboard (highest value, lowest risk)

**Goal:** one screen showing every station's health, captures, uploads, version,
and last-seen — with the *absence* of a heartbeat as the primary "station down"
signal.

**Design:**
- A small ingest service (extend the existing Laravel API, per the tech-stack
  lock) exposing `POST /api/v1/fleet/heartbeat`. Point every station's
  `alert_webhook` at it (already configurable in Settings → Health).
- Store the last N heartbeats per station in Postgres/TimescaleDB (already the
  primary DB). Derive `last_seen`; flag `stale` when `now - last_seen >
  3 × heartbeat_interval`.
- Dashboard (extend the Admin Cockpit — the app already exists) = one row per
  station: status dot (the 8 canonical statuses), captures today, queue depth,
  version, temp, last-seen. Sort/filter by site, status, version.
- **SLO view:** per-station "captured within schedule interval" uptime, upload
  success rate, % of last 24h healthy. These are the numbers that tell you a
  station is drifting *before* the customer notices.

**Effort:** M (mostly wiring existing payloads to a table + a page).
**Dependency:** the Admin Cockpit + Laravel API (Tallium's existing stack).
**Risk:** low — additive, no device changes beyond pointing the webhook.

---

## 2. Safe A/B OTA updates (the hard, essential one)

**Goal:** push a fix to the whole fleet without ever bricking a remote Pi. The
current update paths (curl-reinstall / `update_inplace`) are single-slot: a
failed update on a station 200 km away is a truck roll.

**Design (per §12.5.4):**
- **Two root partitions (A/B).** Update writes the new image to the *inactive*
  slot, sets a one-shot "try B" boot flag, and reboots.
- **Health-gated commit.** On boot, the new slot must pass a self-check
  (service up + `/api/health` ok + camera enumerates within a grace window)
  and "confirm" itself. If it doesn't confirm within a watchdog window, the
  bootloader **auto-rolls back** to the last-known-good slot on the next boot.
  (`tryboot` on Raspberry Pi, or U-Boot `bootcount`/`altbootcmd` on a custom
  boot chain.)
- **Staged rollout.** The fleet service marks a release `canary → 10% → 100%`.
  Stations poll `/api/v1/fleet/desired-version`; a station only self-updates
  when its cohort is eligible. A canary regression halts the rollout.
- **State survives updates.** `/etc/arclap` (PIN + secrets) and `/var/lib/arclap`
  (DB + audit) live on a **separate data partition** never touched by an OTA —
  the same principle the v0.9.9 installer fix enforces for reinstall.

**Effort:** L (image build + bootloader integration + fleet orchestration +
field validation on real hardware).
**Dependency:** pi-gen image with A/B layout (§12.5.1), a second Pi, the fleet
service from §1.
**Risk:** high if rushed — this is the one to build slowly with a soak rig
(§5). Until it exists, keep using health-gated curl-reinstall on a per-station
basis.

---

## 3. Hardware resilience (buy + fit; fixes real classes of failure)

These aren't code — they're the physical foundation, and each kills a failure
mode we've already hit or will:

| Item | Fixes | Priority |
|---|---|---|
| **Powered USB hub (per-port switchable)** between Pi and DSLR | The camera-disconnect class we hit live (current draw browns out the Pi's port). **Also** enables the `uhubctl` power-cycle rung the recovery ladder already supports but can't use today. | **1st** |
| **Official 27 W / 5 A PSU** on every Pi 5 | Pi 5 caps USB current to 600 mA without it → DSLR brownouts | 1st |
| **SSD/USB boot instead of SD** | SD cards wear out under a write-heavy 2-year deployment; SSDs don't | 2nd |
| **Read-only root + overlay (`overlayroot`)** | A power cut can't corrupt a read-only root; only the data partition is writable, and it's WAL-safe + backed up | 2nd (pairs with A/B) |
| **UPS HAT** | `hardware/ups.py::maybe_safe_shutdown` already ships — the HAT just turns it on (clean shutdown < 12% battery) | 3rd |
| **RTC module** | Accurate timestamps when NTP is unreachable on an isolated site | 3rd |

**Effort:** procurement + a field-fit checklist (extend the operator runbook's
pre-deployment section). **Risk:** low, high leverage.

---

## 4. Remote diagnostics + support tunnel

**Goal:** diagnose a misbehaving station without SSH access to a customer LAN.

- **Support-bundle on demand** — `diag.py::run_support_bundle` already writes a
  redacted logs+db+config tarball. Add a fleet-triggered path: the dashboard
  requests one, the station uploads it to a pre-signed S3 URL (outbound-only,
  respecting the security model — no inbound ports).
- **Cloud-mediated tunnel** — per §12.5.6, an outbound-initiated reverse tunnel
  the operator can open *from the fleet side* for live support, time-boxed and
  audited. Phase 2 of the tunnel work already scaffolded (`services/tunnel-*`).
- **Auto-escalation** — the fleet service watches for "no successful capture in
  N schedule intervals" or "queue_failed climbing" and opens a ticket / pages
  before the customer calls.

**Effort:** M. **Risk:** medium (the tunnel needs a careful security review —
it's the one place inbound-ish access is introduced, so it must be
outbound-initiated, short-lived, mTLS, and fully audited).

---

## 5. Test at scale (so you can trust the above)

- **Soak rig** — 3–5 Pis running accelerated schedules for weeks, with fault
  injection (yank USB, cut power, black-hole the uplink, fill the SD). This is
  where the A/B rollback, retention emergency path, and camera recovery ladder
  get proven under real fault sequences instead of unit tests.
- **Real CI** — the audit flagged the "Playwright happy-path" job as fictional
  and the frontend tests as red. Before scaling, make CI real: backend pytest
  (154 tests today) + a genuine frontend build/test + a smoke-deploy to a
  throwaway Pi image. A fleet you can't test is a fleet you can't update.

**Effort:** M–L. **Risk:** low, but it's the gate that makes §2 safe.

---

## Recommended sequence

1. **Powered hub + 5 A PSU** on the current station (fixes the live camera
   issue; unlocks auto power-cycle recovery). — *this week, hardware only*
2. **Fleet dashboard** (§1) — visibility first; it's mostly wiring existing
   heartbeats to a table.
3. **Real CI + soak rig** (§5) — the safety net for everything after.
4. **A/B OTA** (§2) — the big one, built slowly against the soak rig.
5. **SSD boot + read-only root + UPS** (§3) — durability, paired with A/B.
6. **Remote diagnostics + tunnel** (§4) — scale support.

Phases 0–3 made each station *trustworthy*. Phase 4 makes the *fleet*
trustworthy — and it starts with a $30 powered hub, not a rewrite.
