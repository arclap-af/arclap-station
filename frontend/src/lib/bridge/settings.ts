import { z } from "zod";
import { apiFetch } from "../api";
import { arr, obj, strOrNull } from "./json";

// All settings tabs use permissive shapes — the backend exposes a
// subset of what the UI renders, and we fill in sensible defaults so
// the page renders rather than throwing schema errors. As the backend
// grows more telemetry, these adapters pick it up automatically.

export interface GeneralSettings {
  station_name: string;
  site: string;
  gps: string;
  asset_tag: string;
  timezone: string;
  date_format: string;
  language: string;
  ntp_servers: string;
  // v0.8: photo asset value + lifecycle + bandwidth controls
  watermark: boolean;
  dedup_threshold: number | null;
  bandwidth_kbps: number | null;
  project_starts_at: string | null;
  project_ends_at: string | null;
}

export interface NetworkInfo {
  ethernet: { connected: boolean; interface: string; mode: string; ipv4: string; gateway: string; dns: string; mac: string };
  wifi: { connected: boolean; ssid: string; security: string; band: string; signal_dbm: number | null };
  cellular: { status: "up" | "standby" | "down" | "absent"; modem: string; carrier: string; signal_dbm: number | null; apn: string; data_mb: number };
  probes: Array<{ label: string; result: string; level: "ok" | "warn" | "bad" }>;
}

export interface SecurityInfo {
  pin_changed_days_ago: number;
  auto_lock_minutes: number;
  tls: { type: string; fingerprint: string; expires: string; hsts: boolean };
  ssh: { enabled: boolean; port: number; key_count: number; last_login: string | null };
  tokens: Array<{ name: string; prefix: string }>;
}

export interface StorageInfo {
  device: string; fs: string; capacity_bytes: number; used_bytes: number;
  smart: string; buffer_path: string; buffer_max: string; retention: string; when_full: string;
}

export interface SystemInfo {
  hardware: { model: string; serial: string; cpu_pct: number; cpu_temp_c: number; memory_used_mb: number; memory_total_mb: number; ups: string; watchdog: string };
  firmware: { current: string; channel: string; last_check: string; available: string };
  cloud: { paired: boolean; broker: string | null; cockpit_url: string | null };
}

export interface LogEntry { ts: string; unit: string; level: "info" | "warn" | "error"; message: string }

// Legacy exports kept so tabs that import them compile.
export const generalSettingsSchema = z.unknown();
export const networkInfoSchema = z.unknown();
export const securityInfoSchema = z.unknown();
export const storageInfoSchema = z.unknown();
export const systemInfoSchema = z.unknown();
export const logEntrySchema = z.unknown();

function str(v: unknown, fb = "—"): string {
  return typeof v === "string" && v ? v : fb;
}
function num(v: unknown, fb = 0): number {
  return typeof v === "number" ? v : fb;
}

export const settings = {
  async general(): Promise<GeneralSettings> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/settings/general"));
    return {
      station_name: str(raw.name, "Arclap Station"),
      site: str(raw.site, ""),
      gps: raw.lat != null && raw.lon != null ? `${String(raw.lat)}, ${String(raw.lon)}` : "",
      asset_tag: str(raw.serial, ""),
      timezone: str(raw.timezone, "UTC"),
      date_format: "YYYY-MM-DD",
      language: "en",
      ntp_servers: "time.cloudflare.com",
      watermark: Boolean(raw.watermark),
      dedup_threshold: typeof raw.dedup_threshold === "number" ? raw.dedup_threshold : null,
      bandwidth_kbps: typeof raw.bandwidth_kbps === "number" ? raw.bandwidth_kbps : null,
      project_starts_at: typeof raw.project_starts_at === "string" ? raw.project_starts_at : null,
      project_ends_at: typeof raw.project_ends_at === "string" ? raw.project_ends_at : null,
    };
  },
  async saveGeneral(patch: Partial<GeneralSettings>): Promise<GeneralSettings> {
    // Map UI fields back to backend's station-config payload.
    const body: Record<string, unknown> = {};
    if (patch.station_name !== undefined) body.name = patch.station_name;
    if (patch.timezone !== undefined) body.timezone = patch.timezone;
    if (patch.site !== undefined) body.site = patch.site;
    if (patch.watermark !== undefined) body.watermark = patch.watermark;
    if (patch.dedup_threshold !== undefined) body.dedup_threshold = patch.dedup_threshold;
    if (patch.bandwidth_kbps !== undefined) body.bandwidth_kbps = patch.bandwidth_kbps;
    if (patch.project_starts_at !== undefined) body.project_starts_at = patch.project_starts_at;
    if (patch.project_ends_at !== undefined) body.project_ends_at = patch.project_ends_at;
    if (patch.gps !== undefined) {
      const [lat, lon] = patch.gps.split(",").map((s) => Number(s.trim()));
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        body.lat = lat;
        body.lon = lon;
      }
    }
    await apiFetch("/settings/general", { method: "PUT", body });
    return settings.general();
  },
  async network(): Promise<NetworkInfo> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/settings/network"));
    const eth = obj(raw.ethernet);
    const wifi = obj(raw.wifi);
    const cell = obj(raw.cellular);
    return {
      ethernet: {
        connected: Boolean(eth.connected),
        interface: str(eth.interface, "eth0"),
        mode: str(eth.mode, "DHCP"),
        ipv4: str(eth.ipv4, "—"),
        gateway: str(eth.gateway, "—"),
        dns: str(eth.dns, "—"),
        mac: str(eth.mac, "—"),
      },
      wifi: {
        connected: Boolean(wifi.connected),
        ssid: str(wifi.ssid, "—"),
        security: str(wifi.security, "—"),
        band: str(wifi.band, "—"),
        signal_dbm: typeof wifi.signal_dbm === "number" ? wifi.signal_dbm : null,
      },
      cellular: {
        status: (str(cell.status, "absent") as NetworkInfo["cellular"]["status"]),
        modem: str(cell.modem, "—"),
        carrier: str(cell.carrier, "—"),
        signal_dbm: typeof cell.signal_dbm === "number" ? cell.signal_dbm : null,
        apn: str(cell.apn, "—"),
        data_mb: num(cell.data_mb),
      },
      probes: arr(raw.probes).map((entry) => {
        const p = obj(entry);
        return {
          label: str(p.label, ""),
          result: str(p.result, ""),
          level: (str(p.level, "ok") as "ok" | "warn" | "bad"),
        };
      }),
    };
  },
  async security(): Promise<SecurityInfo> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/settings/security"));
    const tls = obj(raw.tls);
    const ssh = obj(raw.ssh);
    const hasAudit = raw.audit_chain != null;
    const audit = obj(raw.audit_chain);
    return {
      pin_changed_days_ago: 0,
      auto_lock_minutes: 15,
      tls: {
        type: str(tls.type, "Caddy self-signed (internal CA)"),
        fingerprint: str(tls.fingerprint, "—"),
        expires: str(tls.expires, "—"),
        hsts: tls.hsts == null ? true : Boolean(tls.hsts),
      },
      ssh: {
        enabled: Boolean(ssh.enabled),
        port: num(ssh.port, 22),
        key_count: num(ssh.key_count, 0),
        last_login: ssh.last_login ? String(ssh.last_login) : null,
      },
      tokens: [
        ...(audit.ok
          ? [{ name: `Audit chain (${num(audit.checked)} entries)`, prefix: "ok" }]
          : hasAudit
            ? [
                {
                  name: `Audit chain (${num(audit.checked)} entries, ${
                    arr(audit.breaks).length
                  } breaks)`,
                  prefix: "WARN",
                },
              ]
            : []),
      ],
    };
  },
  async storage(): Promise<StorageInfo> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/settings/storage"));
    const usedPct = num(raw.disk_used_pct, 0);
    // Use real capacity from backend; fall back to a derived value if missing.
    const cap = num(raw.capacity_bytes, 0) || 64 * 1024 * 1024 * 1024;
    const used = num(raw.used_bytes, 0) || Math.round((usedPct / 100) * cap);
    return {
      device: str(raw.photos_root, "/media/sdcard"),
      fs: str(raw.fs, "ext4"),
      capacity_bytes: cap,
      used_bytes: used,
      smart: "ok",
      buffer_path: str(raw.thumb_root, "/var/lib/arclap/thumbnails"),
      buffer_max: "1 GB",
      retention: "4-tier (hot 7d / warm 30d / cold 90d / archive)",
      when_full: "Auto-purge oldest uploaded",
    };
  },
  async system(): Promise<SystemInfo> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/settings/system"));
    const snap = obj(raw.snapshot);
    const wd = obj(raw.watchdog);
    const ups = obj(raw.ups);
    const cloud = obj(raw.cloud);
    const fw = obj(raw.firmware);
    const upsLabel = ups.detected
      ? `${str(ups.driver)}${ups.battery_pct != null ? ` · ${num(ups.battery_pct)}%` : ""} · ${str(ups.status)}`
      : "not detected";
    const watchdogLabel =
      wd.summary === "active"
        ? `active (kernel ${num(wd.kernel_runtime_sec)}s · service + camera timers)`
        : wd.summary === "partial"
          ? "partial"
          : "inactive";
    return {
      hardware: {
        model: str(raw.hw_model, "—"),
        serial: str(raw.hw_serial, "—"),
        cpu_pct: num(snap.cpu_pct),
        cpu_temp_c: num(snap.cpu_temp_c),
        memory_used_mb:
          typeof snap.mem_used_mb === "number"
            ? snap.mem_used_mb
            : Math.round((num(snap.mem_used_pct) / 100) * num(snap.mem_total_mb, 1)),
        memory_total_mb: num(snap.mem_total_mb, 1),
        ups: upsLabel,
        watchdog: watchdogLabel,
      },
      firmware: {
        current: str(fw.current, str(raw.version, "—")),
        channel: str(fw.channel, "manual"),
        last_check: fw.last_check ? str(fw.last_check, "—") : "—",
        available: fw.available ? str(fw.available, "—") : "—",
      },
      cloud: {
        paired: Boolean(cloud.paired),
        broker: strOrNull(cloud.broker),
        cockpit_url: strOrNull(cloud.cockpit_url),
      },
    };
  },
  async logs(unit?: string, level?: string, query?: string): Promise<LogEntry[]> {
    // Hit the journald-backed /logs/recent endpoint (NOT /audit/recent
    // — that was the source of the schema mismatch: the cockpit
    // expected `unit` and `level`, but audit records had `actor` and
    // no `level`, so every filter dropdown was a no-op). Backend
    // already returns newest-first, so the cockpit can render in array
    // order.
    const qs = new URLSearchParams();
    if (unit && unit !== "all") qs.set("unit", unit);
    if (level && level !== "all") qs.set("level", level);
    if (query) qs.set("q", query);
    try {
      const raw = await apiFetch<unknown>(
        `/settings/logs/recent${qs.toString() ? `?${qs}` : ""}`,
      );
      if (!Array.isArray(raw)) return [];
      return raw.map((entry) => {
        const e = obj(entry);
        return {
          ts: str(e.ts, ""),
          unit: str(e.unit, "system"),
          level: (str(e.level, "info") as LogEntry["level"]),
          message: str(e.message, ""),
        };
      });
    } catch {
      return [];
    }
  },
  async restart(unit: string = "arclap-station"): Promise<void> {
    await apiFetch("/settings/restart-service", { method: "POST", body: { unit } });
  },
  async reboot(confirmPin: string): Promise<void> {
    await apiFetch("/settings/reboot", {
      method: "POST",
      body: { confirm_pin: confirmPin },
    });
  },
  async factoryReset(confirmPin: string, purgePhotos = false): Promise<void> {
    await apiFetch("/settings/factory-reset", {
      method: "POST",
      body: { confirm_pin: confirmPin, purge_photos: purgePhotos },
    });
  },
};
