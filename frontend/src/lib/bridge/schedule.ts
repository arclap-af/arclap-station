import { z } from "zod";
import { apiFetch, apiJson } from "../api";

// Flat shape the Schedule UI renders.
export interface Schedule {
  id: string;
  name: string;
  interval_minutes: number;
  from_time: string;
  to_time: string;
  days: Array<"mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun">;
  enabled: boolean;
  skip_disk_full: boolean;
  skip_destinations_offline: boolean;
  // When true (default), the SD-card copy of each photo stays after
  // upload. When false, the photo is removed once every destination
  // has acknowledged it — useful when an external FTP/S3 is the
  // canonical store and the SD card is just the staging buffer.
  keep_local: boolean;
  destination_id: string | null;
  destination_label: string;
  next_fire_at: string | null;
}
export type ScheduleDraft = Omit<Schedule, "id" | "next_fire_at"> & { id?: string };

// Backend uses `interval_min`; keep schema permissive.
export const scheduleSchema = z.unknown();

const DAY_SET = new Set(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const);

function adaptSchedule(raw: Record<string, any>): Schedule {
  const rawDays = Array.isArray(raw.days) ? raw.days : [];
  const days = rawDays.filter((d: unknown): d is Schedule["days"][number] =>
    typeof d === "string" && DAY_SET.has(d as Schedule["days"][number]),
  );
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? "Schedule"),
    interval_minutes:
      typeof raw.interval_min === "number"
        ? raw.interval_min
        : typeof raw.interval_minutes === "number"
          ? raw.interval_minutes
          : 15,
    from_time: String(raw.from_time ?? "06:00"),
    to_time: String(raw.to_time ?? "19:00"),
    days,
    enabled: Boolean(raw.enabled),
    skip_disk_full: Boolean(raw.skip_disk_full ?? raw.conditions?.skip_disk_full),
    skip_destinations_offline: Boolean(
      raw.skip_destinations_offline ?? raw.conditions?.skip_destinations_offline,
    ),
    destination_id:
      typeof raw.destination_id === "string"
        ? raw.destination_id
        : typeof raw.dest_filter === "string"
          ? raw.dest_filter
          : null,
    destination_label:
      typeof raw.destination_label === "string"
        ? raw.destination_label
        : raw.dest_filter
          ? String(raw.dest_filter)
          : "All",
    // Backend emits this flat key from the conditions JSON; default
    // True keeps existing pre-feature schedules in their safe state.
    keep_local: typeof raw.keep_local === "boolean" ? raw.keep_local : true,
    next_fire_at: typeof raw.next_fire_at === "string" ? raw.next_fire_at : null,
  };
}

// Translate UI fields → backend payload (rename interval_minutes →
// interval_min etc.). All persisted fields go through here — earlier
// versions of this function silently dropped skip_disk_full and
// skip_destinations_offline, so the toggles on the form were pure
// theatre. Both are now wired through to ScheduleCreate/UpdateRequest
// on the backend, which merges them into the schedule's conditions
// JSON, which fire_capture() then honours.
function toBackendPayload(p: ScheduleDraft): Record<string, unknown> {
  return {
    name: p.name,
    interval_min: p.interval_minutes,
    from_time: p.from_time,
    to_time: p.to_time,
    days: p.days,
    enabled: p.enabled,
    dest_filter: p.destination_id,
    skip_disk_full: p.skip_disk_full,
    skip_destinations_offline: p.skip_destinations_offline,
    keep_local: p.keep_local,
  };
}

export const schedule = {
  async list(): Promise<Schedule[]> {
    const raw = await apiJson("/schedule/list", z.array(z.record(z.unknown())));
    return raw.map(adaptSchedule);
  },
  async create(payload: ScheduleDraft): Promise<Schedule> {
    const raw = await apiJson("/schedule/create", z.record(z.unknown()), {
      method: "POST",
      body: toBackendPayload(payload),
    });
    return adaptSchedule(raw);
  },
  async update(id: string, payload: ScheduleDraft): Promise<Schedule> {
    const raw = await apiJson(`/schedule/${id}`, z.record(z.unknown()), {
      method: "PUT",
      body: toBackendPayload(payload),
    });
    return adaptSchedule(raw);
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/schedule/${id}`, { method: "DELETE" });
  },
  async setEnabled(id: string, enabled: boolean): Promise<Schedule> {
    // Backend has no per-flag toggle endpoint — do a PUT with just enabled.
    const raw = await apiJson(`/schedule/${id}`, z.record(z.unknown()), {
      method: "PUT",
      body: { enabled },
    });
    return adaptSchedule(raw);
  },
};
