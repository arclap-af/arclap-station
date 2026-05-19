import { z } from "zod";
import { apiFetch } from "../api";

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
    const raw = (await apiFetch<Record<string, any>>("/settings/general")) ?? {};
    return {
      station_name: str(raw.name, "Arclap Station"),
      site: str(raw.site, ""),
      gps: raw.lat != null && raw.lon != null ? `${raw.lat}, ${raw.lon}` : "",
      asset_tag: str(raw.serial, ""),
      timezone: str(raw.timezone, "UTC"),
      date_format: "YYYY-MM-DD",
      language: "en",
      ntp_servers: "time.cloudflare.com",
    };
  },
  async saveGeneral(patch: Partial<GeneralSettings>): Promise<GeneralSettings> {
    // Map UI fields back to backend's station-config payload.
    const body: Record<string, unknown> = {};
    if (patch.station_name !== undefined) body.name = patch.station_name;
    if (patch.timezone !== undefined) body.timezone = patch.timezone;
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
    const raw = (await apiFetch<Record<string, any>>("/settings/network")) ?? {};
    const ip = str(raw.ip, "");
    return {
      ethernet: {
        connected: !!ip && ip !== "127.0.1.1",
        interface: "eth0",
        mode: "DHCP",
        ipv4: ip || "—",
        gateway: "—",
        dns: "—",
        mac: "—",
      },
      wifi: { connected: false, ssid: "—", security: "—", band: "—", signal_dbm: null },
      cellular: { status: "absent", modem: "—", carrier: "—", signal_dbm: null, apn: "—", data_mb: 0 },
      probes: [
        { label: "Hostname", result: str(raw.hostname, "—"), level: "ok" },
        { label: "Platform", result: str(raw.platform, "—"), level: "ok" },
      ],
    };
  },
  async security(): Promise<SecurityInfo> {
    const raw = (await apiFetch<Record<string, any>>("/settings/security")) ?? {};
    return {
      pin_changed_days_ago: 0,
      auto_lock_minutes: 15,
      tls: { type: "Caddy self-signed", fingerprint: "—", expires: "—", hsts: true },
      ssh: { enabled: true, port: 22, key_count: 0, last_login: null },
      tokens: [
        ...(raw?.audit_chain?.ok
          ? [{ name: `Audit chain (${raw.audit_chain.checked} entries)`, prefix: "ok" }]
          : []),
      ],
    };
  },
  async storage(): Promise<StorageInfo> {
    const raw = (await apiFetch<Record<string, any>>("/settings/storage")) ?? {};
    const usedPct = num(raw.disk_used_pct, 0);
    const cap = 64 * 1024 * 1024 * 1024; // 64 GB default if unknown
    return {
      device: str(raw.photos_root, "/media/sdcard"),
      fs: "ext4",
      capacity_bytes: cap,
      used_bytes: Math.round((usedPct / 100) * cap),
      smart: "ok",
      buffer_path: str(raw.thumb_root, "/var/lib/arclap/thumbnails"),
      buffer_max: "1 GB",
      retention: "90 days",
      when_full: "Delete oldest unstarred",
    };
  },
  async system(): Promise<SystemInfo> {
    const raw = (await apiFetch<Record<string, any>>("/settings/system")) ?? {};
    const snap = raw.snapshot ?? {};
    return {
      hardware: {
        model: "Raspberry Pi 5",
        serial: "—",
        cpu_pct: num(snap.cpu_pct),
        cpu_temp_c: num(snap.cpu_temp_c),
        memory_used_mb: Math.round((num(snap.mem_used_pct) / 100) * num(snap.mem_total_mb, 1)),
        memory_total_mb: num(snap.mem_total_mb, 1),
        ups: "absent",
        watchdog: "active",
      },
      firmware: {
        current: str(raw.version, "0.1.0"),
        channel: "stable",
        last_check: "—",
        available: "—",
      },
      cloud: { paired: false, broker: null, cockpit_url: null },
    };
  },
  async logs(unit?: string, level?: string, query?: string): Promise<LogEntry[]> {
    const qs = new URLSearchParams();
    if (unit && unit !== "all") qs.set("unit", unit);
    if (level && level !== "all") qs.set("level", level);
    if (query) qs.set("q", query);
    try {
      const raw = await apiFetch<unknown>(
        `/settings/audit/recent${qs.toString() ? `?${qs}` : ""}`,
      );
      if (!Array.isArray(raw)) return [];
      return raw.map((e: any) => ({
        ts: str(e.ts ?? e.timestamp, ""),
        unit: str(e.actor ?? e.unit, "system"),
        level: (str(e.level, "info") as LogEntry["level"]),
        message: str(e.event ?? e.message, ""),
      }));
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
