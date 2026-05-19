import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const scheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  interval_minutes: z.number().int().positive(),
  from_time: z.string(),
  to_time: z.string(),
  days: z.array(z.enum(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])),
  enabled: z.boolean(),
  skip_disk_full: z.boolean(),
  skip_destinations_offline: z.boolean(),
  destination_id: z.string().nullable(),
  destination_label: z.string(),
  next_fire_at: z.string().nullable(),
});
export type Schedule = z.infer<typeof scheduleSchema>;

export type ScheduleDraft = Omit<Schedule, "id" | "next_fire_at"> & { id?: string };

export const schedule = {
  async list(): Promise<Schedule[]> {
    return apiJson("/schedule", z.array(scheduleSchema));
  },
  async create(payload: ScheduleDraft): Promise<Schedule> {
    return apiJson("/schedule", scheduleSchema, { method: "POST", body: payload });
  },
  async update(id: string, payload: ScheduleDraft): Promise<Schedule> {
    return apiJson(`/schedule/${id}`, scheduleSchema, { method: "PUT", body: payload });
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/schedule/${id}`, { method: "DELETE" });
  },
  async setEnabled(id: string, enabled: boolean): Promise<Schedule> {
    return apiJson(`/schedule/${id}/enabled`, scheduleSchema, { method: "POST", body: { enabled } });
  },
};
