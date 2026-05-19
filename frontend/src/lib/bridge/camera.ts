import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const cameraSettingsSchema = z.object({
  mode: z.string(),
  iso: z.string(),
  shutter: z.string(),
  aperture: z.string(),
  ev: z.string(),
  wb: z.string(),
  kelvin: z.number().int(),
  drive: z.string(),
  quality: z.string(),
  focus: z.string(),
  af_area: z.string(),
  metering: z.string(),
  picture_style: z.string(),
  color_space: z.string(),
  aspect: z.string(),
});
export type CameraSettings = z.infer<typeof cameraSettingsSchema>;

export const cameraPropSchema = z.object({
  path: z.string(),
  value: z.string(),
  writable: z.boolean(),
  choices: z.string().optional().nullable(),
});
export type CameraProp = z.infer<typeof cameraPropSchema>;

export const camera = {
  async settings(): Promise<CameraSettings> {
    return apiJson("/camera/settings", cameraSettingsSchema);
  },
  async updateSettings(patch: Partial<CameraSettings>): Promise<CameraSettings> {
    return apiJson("/camera/settings", cameraSettingsSchema, { method: "POST", body: patch });
  },
  async capture(): Promise<{ id: string; filename: string; size_bytes: number }> {
    return apiFetch("/camera/capture", { method: "POST" });
  },
  async reconnect(): Promise<{ ok: boolean }> {
    return apiFetch("/camera/reconnect", { method: "POST" });
  },
  async syncClock(): Promise<void> {
    await apiFetch("/camera/sync-clock", { method: "POST" });
  },
  async usbReset(): Promise<void> {
    await apiFetch("/camera/usb-reset", { method: "POST" });
  },
  async properties(): Promise<CameraProp[]> {
    return apiJson("/camera/properties", z.array(cameraPropSchema));
  },
  async setProperty(path: string, value: string): Promise<void> {
    await apiFetch("/camera/properties", { method: "POST", body: { path, value } });
  },
};
