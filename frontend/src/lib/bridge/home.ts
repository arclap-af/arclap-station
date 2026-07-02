import { z } from "zod";
import { apiFetch } from "../api";

// What the Home / Shell components consume. Fields are all optional or
// have defaults — the dashboard renders gracefully on missing data.
export interface Telemetry {
  hostname: string;
  ip: string;
  serial: string;
  firmware: string;
  uptime_seconds: number;
  status: "online" | "warn" | "offline";
  last_sync_seconds_ago: number;
  captures_today: number;
  next_capture_seconds: number | null;
  queue_pending: number;
  queue_failed: number;
  avg_upload_seconds: number;
  storage_used_pct: number;
  storage_free_bytes: number;
  cpu_pct: number;
  cpu_temp_c: number;
  memory_used_mb: number;
  memory_total_mb: number;
  network_throughput_mbps: number;
  network_signal_dbm: number | null;
  ups_pct: number | null;
  ups_status: string | null;
  camera: {
    detected: boolean;
    model: string | null;
    lens: string | null;
    firmware: string | null;
    battery_pct: number | null;
    shutter_count: number | null;
    sensor_temp_c: number | null;
    usb_port: string | null;
    driver: string | null;
  } | null;
}

export interface ActivityEvent {
  ts: string;
  service: string;
  level: "info" | "warn" | "error";
  message: string;
}

// Kept for backwards compat with imports — but the schemas are
// permissive now because the backend's snapshot shape is a moving
// target across releases and we don't want a hard schema fail to take
// down the dashboard.
export const telemetrySchema = z.unknown();
export const activityEventSchema = z.object({
  ts: z.string(),
  service: z.string(),
  level: z.enum(["info", "warn", "error"]),
  message: z.string(),
});

// Translate the backend `/api/home` snapshot (defined in
// backend/arclap_station/api/home.py:_build_snapshot) into the shape
// the dashboard wants. Missing fields fall back to safe defaults so
// the UI keeps rendering even if a future backend trims its output.
//
// Exported because the Home page also receives WebSocket pushes that
// carry the same raw shape and need the same adaptation.
export function adaptTelemetry(raw: Record<string, unknown>): Telemetry {
  const camRaw = (raw["camera"] as Record<string, unknown> | undefined) ?? null;
  const stationRaw = (raw["station"] as Record<string, unknown> | undefined) ?? {};
  const queueStats = (raw["queue_stats"] as Record<string, unknown> | undefined) ?? {};
  const num = (k: string, fallback = 0): number => {
    const v = raw[k];
    return typeof v === "number" ? v : fallback;
  };
  const queue = (k: string, fallback = 0): number => {
    const v = queueStats[k];
    return typeof v === "number" ? v : fallback;
  };
  const str = (src: Record<string, unknown>, k: string, fallback: string): string => {
    const v = src[k];
    return typeof v === "string" ? v : fallback;
  };
  return {
    hostname: str(stationRaw, "hostname", "arclap-station"),
    ip: typeof raw["ip"] === "string" ? (raw["ip"] as string) : "—",
    serial: typeof raw["serial"] === "string" ? (raw["serial"] as string) : "",
    firmware:
      typeof raw["firmware"] === "string"
        ? (raw["firmware"] as string)
        : typeof raw["version"] === "string"
          ? (raw["version"] as string)
          : "—",
    uptime_seconds: num("uptime_seconds"),
    // status is now backend-derived (camera + queue + disk + uptime).
    status: ((): Telemetry["status"] => {
      const s = raw["status"];
      if (s === "online" || s === "warn" || s === "offline") return s;
      return "online";
    })(),
    last_sync_seconds_ago: num("last_sync_seconds_ago"),
    captures_today: num("captures_24h"),
    next_capture_seconds:
      typeof raw["next_capture_seconds"] === "number"
        ? (raw["next_capture_seconds"] as number)
        : null,
    // Prefer the dedicated top-level counters, fall back to nested queue_stats.
    queue_pending: typeof raw["queue_pending"] === "number"
      ? (raw["queue_pending"] as number)
      : num("queue_depth"),
    queue_failed: typeof raw["queue_failed"] === "number"
      ? (raw["queue_failed"] as number)
      : queue("failed"),
    avg_upload_seconds: queue("avg_upload_seconds"),
    // storage_used_pct still maps to disk_used_pct; storage_free_bytes
    // now comes from a real metric.
    storage_used_pct: num("disk_used_pct", num("storage_used_pct")),
    storage_free_bytes: num("disk_free_bytes", num("storage_free_bytes")),
    cpu_pct: num("cpu_pct"),
    cpu_temp_c: num("cpu_temp_c"),
    // mem_used_mb is new in v0.4; previously we derived it from the pct.
    memory_used_mb:
      typeof raw["mem_used_mb"] === "number"
        ? (raw["mem_used_mb"] as number)
        : Math.round((num("mem_used_pct") / 100) * num("mem_total_mb", 1)),
    memory_total_mb: num("mem_total_mb", num("memory_total_mb", 1)),
    network_throughput_mbps: num("network_throughput_mbps"),
    network_signal_dbm: typeof raw["network_signal_dbm"] === "number"
      ? (raw["network_signal_dbm"] as number)
      : null,
    ups_pct: typeof raw["ups_pct"] === "number" ? (raw["ups_pct"] as number) : null,
    ups_status: typeof raw["ups_status"] === "string" ? (raw["ups_status"] as string) : null,
    camera: camRaw
      ? {
          detected: Boolean(camRaw["detected"]),
          model: typeof camRaw["model"] === "string" ? (camRaw["model"] as string) : null,
          lens: typeof camRaw["lens"] === "string" ? (camRaw["lens"] as string) : null,
          firmware:
            typeof camRaw["firmware"] === "string" ? (camRaw["firmware"] as string) : null,
          battery_pct:
            typeof camRaw["battery"] === "number" ? (camRaw["battery"] as number) : null,
          shutter_count:
            typeof camRaw["shutter_count"] === "number"
              ? (camRaw["shutter_count"] as number)
              : null,
          sensor_temp_c: null,
          usb_port: typeof camRaw["port"] === "string" ? (camRaw["port"] as string) : null,
          driver: "gphoto2",
        }
      : null,
  };
}

export const home = {
  async telemetry(): Promise<Telemetry> {
    const raw = await apiFetch<Record<string, unknown>>("/home");
    return adaptTelemetry(raw);
  },
  async activity(limit = 25): Promise<ActivityEvent[]> {
    // Real audit log feed. Backend returns rows shaped like
    // {ts, actor, event, details_json, ...}. Map into the UI's
    // ActivityEvent shape.
    try {
      const raw = await apiFetch<Array<Record<string, unknown>>>(
        `/home/activity?limit=${Math.max(1, Math.min(200, limit))}`,
      );
      if (!Array.isArray(raw)) return [];
      return raw.map((e) => ({
        ts: String(e.ts ?? e.timestamp ?? ""),
        service: String(e.actor ?? e.service ?? "system"),
        level: ((): "info" | "warn" | "error" => {
          const ev = String(e.event ?? "");
          if (ev.includes("error") || ev.includes("failed") || ev.includes("crash"))
            return "error";
          if (
            ev.includes("warn") ||
            ev.includes("watchdog") ||
            ev.includes("locked_out") ||
            ev.includes("invalid")
          )
            return "warn";
          return "info";
        })(),
        message: String(e.event ?? e.message ?? ""),
      }));
    } catch {
      return [];
    }
  },
};
