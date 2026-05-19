import { z } from "zod";
import { apiFetch, apiJson } from "../api";

// Matches backend setup_status() in setup_wizard.py.
export const setupStatusSchema = z.object({
  first_boot: z.boolean(),
  pin_set: z.boolean(),
  station_named: z.boolean(),
  completed: z.boolean(),
});
export type SetupStatus = z.infer<typeof setupStatusSchema>;

// Matches backend setup_network_check() response.
export const networkProbeSchema = z.object({
  ok: z.boolean(),
  icmp: z.boolean(),
  dns: z.boolean(),
  https: z.boolean(),
  ntp: z.boolean(),
});
export type NetworkProbe = z.infer<typeof networkProbeSchema>;

export const setup = {
  async status(): Promise<SetupStatus> {
    return apiJson("/setup/status", setupStatusSchema);
  },
  async setPin(pin: string): Promise<void> {
    await apiFetch("/setup/pin", { method: "POST", body: { pin } });
  },
  async detectCamera(): Promise<{
    detected: boolean;
    model?: string;
    lens?: string;
    firmware?: string;
    battery?: number;
    shutter_count?: number;
  }> {
    return apiFetch("/setup/camera-detect", { method: "POST" });
  },
  async station(name: string, timezone: string): Promise<void> {
    await apiFetch("/setup/station", { method: "POST", body: { name, timezone } });
  },
  async network(): Promise<NetworkProbe> {
    // Backend route is POST /setup/network-check (not GET /setup/network).
    return apiJson("/setup/network-check", networkProbeSchema, { method: "POST" });
  },
  async destination(payload: { type: string; config: Record<string, unknown> }): Promise<unknown> {
    // Backend route is /setup/destination-test, body { type, config }.
    return apiFetch("/setup/destination-test", { method: "POST", body: payload });
  },
  async schedule(payload: {
    interval_min: number;
    from_time: string;
    to_time: string;
    days: string[];
    name?: string;
  }): Promise<void> {
    await apiFetch("/setup/schedule", { method: "POST", body: payload });
  },
  async pair(pair_code: string): Promise<void> {
    // Backend requires pair_code (min 4 chars). Callers should skip the
    // call entirely if pairing is disabled.
    await apiFetch("/setup/pair", { method: "POST", body: { pair_code } });
  },
  async finish(): Promise<void> {
    await apiFetch("/setup/finish", { method: "POST" });
  },
};
