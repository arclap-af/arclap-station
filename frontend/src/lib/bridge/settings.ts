import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const generalSettingsSchema = z.object({
  station_name: z.string(),
  site: z.string(),
  gps: z.string(),
  asset_tag: z.string(),
  timezone: z.string(),
  date_format: z.string(),
  language: z.string(),
  ntp_servers: z.string(),
});
export type GeneralSettings = z.infer<typeof generalSettingsSchema>;

export const networkInfoSchema = z.object({
  ethernet: z.object({
    connected: z.boolean(),
    interface: z.string(),
    mode: z.string(),
    ipv4: z.string(),
    gateway: z.string(),
    dns: z.string(),
    mac: z.string(),
  }),
  wifi: z.object({
    connected: z.boolean(),
    ssid: z.string(),
    security: z.string(),
    band: z.string(),
    signal_dbm: z.number().nullable(),
  }),
  cellular: z.object({
    status: z.enum(["up", "standby", "down", "absent"]),
    modem: z.string(),
    carrier: z.string(),
    signal_dbm: z.number().nullable(),
    apn: z.string(),
    data_mb: z.number(),
  }),
  probes: z.array(
    z.object({
      label: z.string(),
      result: z.string(),
      level: z.enum(["ok", "warn", "bad"]),
    }),
  ),
});
export type NetworkInfo = z.infer<typeof networkInfoSchema>;

export const securityInfoSchema = z.object({
  pin_changed_days_ago: z.number().int(),
  auto_lock_minutes: z.number().int(),
  tls: z.object({
    type: z.string(),
    fingerprint: z.string(),
    expires: z.string(),
    hsts: z.boolean(),
  }),
  ssh: z.object({
    enabled: z.boolean(),
    port: z.number().int(),
    key_count: z.number().int(),
    last_login: z.string().nullable(),
  }),
  tokens: z.array(z.object({ name: z.string(), prefix: z.string() })),
});
export type SecurityInfo = z.infer<typeof securityInfoSchema>;

export const storageInfoSchema = z.object({
  device: z.string(),
  fs: z.string(),
  capacity_bytes: z.number(),
  used_bytes: z.number(),
  smart: z.string(),
  buffer_path: z.string(),
  buffer_max: z.string(),
  retention: z.string(),
  when_full: z.string(),
});
export type StorageInfo = z.infer<typeof storageInfoSchema>;

export const systemInfoSchema = z.object({
  hardware: z.object({
    model: z.string(),
    serial: z.string(),
    cpu_pct: z.number(),
    cpu_temp_c: z.number(),
    memory_used_mb: z.number(),
    memory_total_mb: z.number(),
    ups: z.string(),
    watchdog: z.string(),
  }),
  firmware: z.object({
    current: z.string(),
    channel: z.string(),
    last_check: z.string(),
    available: z.string(),
  }),
  cloud: z.object({
    paired: z.boolean(),
    broker: z.string().nullable(),
    cockpit_url: z.string().nullable(),
  }),
});
export type SystemInfo = z.infer<typeof systemInfoSchema>;

export const logEntrySchema = z.object({
  ts: z.string(),
  unit: z.string(),
  level: z.enum(["info", "warn", "error"]),
  message: z.string(),
});
export type LogEntry = z.infer<typeof logEntrySchema>;

export const settings = {
  async general(): Promise<GeneralSettings> {
    return apiJson("/settings/general", generalSettingsSchema);
  },
  async saveGeneral(patch: Partial<GeneralSettings>): Promise<GeneralSettings> {
    return apiJson("/settings/general", generalSettingsSchema, { method: "PUT", body: patch });
  },
  async network(): Promise<NetworkInfo> {
    return apiJson("/settings/network", networkInfoSchema);
  },
  async security(): Promise<SecurityInfo> {
    return apiJson("/settings/security", securityInfoSchema);
  },
  async storage(): Promise<StorageInfo> {
    return apiJson("/settings/storage", storageInfoSchema);
  },
  async system(): Promise<SystemInfo> {
    return apiJson("/settings/system", systemInfoSchema);
  },
  async logs(unit?: string, level?: string, query?: string): Promise<LogEntry[]> {
    const qs = new URLSearchParams();
    if (unit && unit !== "all") qs.set("unit", unit);
    if (level && level !== "all") qs.set("level", level);
    if (query) qs.set("q", query);
    return apiJson(`/settings/logs${qs.toString() ? `?${qs}` : ""}`, z.array(logEntrySchema));
  },
  async restart(unit: string): Promise<void> {
    await apiFetch("/settings/restart", { method: "POST", body: { unit } });
  },
  async reboot(): Promise<void> {
    await apiFetch("/settings/reboot", { method: "POST" });
  },
  async factoryReset(): Promise<void> {
    await apiFetch("/settings/factory-reset", { method: "POST" });
  },
};
