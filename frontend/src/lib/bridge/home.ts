import { z } from "zod";
import { apiJson } from "../api";

export const telemetrySchema = z.object({
  hostname: z.string(),
  ip: z.string(),
  serial: z.string(),
  firmware: z.string(),
  uptime_seconds: z.number(),
  status: z.enum(["online", "warn", "offline"]),
  last_sync_seconds_ago: z.number(),
  captures_today: z.number().int(),
  next_capture_seconds: z.number().int().nullable(),
  queue_pending: z.number().int(),
  queue_failed: z.number().int(),
  avg_upload_seconds: z.number(),
  storage_used_pct: z.number(),
  storage_free_bytes: z.number(),
  cpu_pct: z.number(),
  cpu_temp_c: z.number(),
  memory_used_mb: z.number(),
  memory_total_mb: z.number(),
  network_throughput_mbps: z.number(),
  network_signal_dbm: z.number().nullable(),
  ups_pct: z.number().nullable(),
  ups_status: z.string().nullable(),
  camera: z
    .object({
      detected: z.boolean(),
      model: z.string().nullable(),
      lens: z.string().nullable(),
      firmware: z.string().nullable(),
      battery_pct: z.number().nullable(),
      shutter_count: z.number().int().nullable(),
      sensor_temp_c: z.number().nullable(),
      usb_port: z.string().nullable(),
      driver: z.string().nullable(),
    })
    .nullable(),
});
export type Telemetry = z.infer<typeof telemetrySchema>;

export const activityEventSchema = z.object({
  ts: z.string(),
  service: z.string(),
  level: z.enum(["info", "warn", "error"]),
  message: z.string(),
});
export type ActivityEvent = z.infer<typeof activityEventSchema>;

export const home = {
  async telemetry(): Promise<Telemetry> {
    return apiJson("/home/telemetry", telemetrySchema);
  },
  async activity(limit = 10): Promise<ActivityEvent[]> {
    return apiJson(`/home/activity?limit=${limit}`, z.array(activityEventSchema));
  },
};
