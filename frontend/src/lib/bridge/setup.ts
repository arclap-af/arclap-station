import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const setupStatusSchema = z.object({
  first_boot: z.boolean(),
  step: z.string(),
  completed_steps: z.array(z.string()),
});
export type SetupStatus = z.infer<typeof setupStatusSchema>;

export const networkProbeSchema = z.object({
  eth: z.enum(["up", "down", "standby"]),
  wifi: z.enum(["up", "down", "standby"]),
  cell: z.enum(["up", "down", "standby"]),
  ntp: z.enum(["ok", "drift", "down"]),
});
export type NetworkProbe = z.infer<typeof networkProbeSchema>;

export const setup = {
  async status(): Promise<SetupStatus> {
    return apiJson("/setup/status", setupStatusSchema);
  },
  async setPin(pin: string): Promise<void> {
    await apiFetch("/setup/pin", { method: "POST", body: { pin } });
  },
  async detectCamera(): Promise<{ detected: boolean; model?: string; lens?: string; firmware?: string; battery?: number; shutter_count?: number }> {
    return apiFetch("/setup/camera-detect", { method: "POST" });
  },
  async station(name: string, timezone: string): Promise<void> {
    await apiFetch("/setup/station", { method: "POST", body: { name, timezone } });
  },
  async network(): Promise<NetworkProbe> {
    return apiJson("/setup/network", networkProbeSchema);
  },
  async destination(payload: Record<string, unknown>): Promise<void> {
    await apiFetch("/setup/destination", { method: "POST", body: payload });
  },
  async schedule(payload: Record<string, unknown>): Promise<void> {
    await apiFetch("/setup/schedule", { method: "POST", body: payload });
  },
  async pair(payload: { code: string | null; enabled: boolean }): Promise<void> {
    await apiFetch("/setup/pair", { method: "POST", body: payload });
  },
  async finish(): Promise<void> {
    await apiFetch("/setup/finish", { method: "POST" });
  },
};
