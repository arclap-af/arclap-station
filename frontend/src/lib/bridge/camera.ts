import { z } from "zod";
import { apiFetch } from "../api";

// Flat shape the Camera page renders. All fields are strings to dodge
// the variability of gphoto2 widget value types across camera models.
export interface CameraSettings {
  mode: string;
  iso: string;
  shutter: string;
  aperture: string;
  ev: string;
  wb: string;
  kelvin: number;
  drive: string;
  quality: string;
  focus: string;
  af_area: string;
  metering: string;
  picture_style: string;
  color_space: string;
  aspect: string;
}

export const cameraSettingsSchema = z.unknown();

export const cameraPropSchema = z.object({
  path: z.string(),
  label: z.string().optional(),
  type: z.string().optional(),
  value: z.union([z.string(), z.number(), z.boolean()]).optional().nullable(),
  choices: z.array(z.string()).optional().nullable(),
  readonly: z.boolean().optional(),
});
export type CameraProp = z.infer<typeof cameraPropSchema>;

// Extract a string value from gphoto2's nested widget tree. The backend
// returns `{ "/main/imgsettings/iso": {value:"400", choices:[...]}, ... }`.
function adaptSettings(raw: Record<string, any>): CameraSettings {
  const get = (path: string, fallback = "—"): string => {
    const w = raw?.[path];
    if (!w) return fallback;
    const v = w.value;
    if (v === null || v === undefined || v === "") return fallback;
    return String(v);
  };
  const num = (path: string, fallback: number): number => {
    const s = get(path, "");
    const n = parseInt(s, 10);
    return Number.isFinite(n) ? n : fallback;
  };
  return {
    mode: get("/main/capturesettings/autoexposuremode") || get("/main/capturesettings/shootmode") || "M",
    iso: get("/main/imgsettings/iso"),
    shutter: get("/main/capturesettings/shutterspeed"),
    aperture: get("/main/capturesettings/aperture"),
    ev: get("/main/capturesettings/exposurecompensation"),
    wb: get("/main/imgsettings/whitebalance"),
    kelvin: num("/main/imgsettings/colortemperature", 5500),
    drive: get("/main/capturesettings/drivemode"),
    quality: get("/main/imgsettings/imageformat") || get("/main/imgsettings/imagequality"),
    focus: get("/main/capturesettings/focusmode"),
    af_area: get("/main/capturesettings/afmethod"),
    metering: get("/main/capturesettings/meteringmode"),
    picture_style: get("/main/imgsettings/picturestyle"),
    color_space: get("/main/imgsettings/colorspace"),
    aspect: get("/main/imgsettings/aspectratio"),
  };
}

// Map flat-shape PATCH keys to the gphoto2 widget paths the backend's
// PUT /camera/settings expects.
const FLAT_TO_PATH: Record<keyof CameraSettings, string> = {
  mode: "/main/capturesettings/autoexposuremode",
  iso: "/main/imgsettings/iso",
  shutter: "/main/capturesettings/shutterspeed",
  aperture: "/main/capturesettings/aperture",
  ev: "/main/capturesettings/exposurecompensation",
  wb: "/main/imgsettings/whitebalance",
  kelvin: "/main/imgsettings/colortemperature",
  drive: "/main/capturesettings/drivemode",
  quality: "/main/imgsettings/imageformat",
  focus: "/main/capturesettings/focusmode",
  af_area: "/main/capturesettings/afmethod",
  metering: "/main/capturesettings/meteringmode",
  picture_style: "/main/imgsettings/picturestyle",
  color_space: "/main/imgsettings/colorspace",
  aspect: "/main/imgsettings/aspectratio",
};

// Real camera state + actual gphoto2 choices for the chip rows.
export interface CameraInfo {
  detected: boolean;
  model: string | null;
  lens: string | null;
  battery: string | null;
  port: string | null;
  shutter_count: number | null;
  values: Partial<Record<keyof CameraSettings, string>>;
  choices: Partial<Record<keyof CameraSettings, string[]>>;
}

export const camera = {
  async info(): Promise<CameraInfo> {
    const raw = await apiFetch<Record<string, any>>("/camera/info");
    return {
      detected: Boolean(raw?.detected),
      model: raw?.model ?? null,
      lens: raw?.lens ?? null,
      battery: raw?.battery ?? null,
      port: raw?.port ?? null,
      shutter_count: typeof raw?.shutter_count === "number" ? raw.shutter_count : null,
      values: (raw?.values ?? {}) as Partial<Record<keyof CameraSettings, string>>,
      choices: (raw?.choices ?? {}) as Partial<Record<keyof CameraSettings, string[]>>,
    };
  },
  async settings(): Promise<CameraSettings> {
    const raw = await apiFetch<Record<string, any>>("/camera/settings");
    return adaptSettings(raw);
  },
  async updateSettings(patch: Partial<CameraSettings>): Promise<CameraSettings> {
    // Backend takes PUT, one widget at a time. Send them serially.
    for (const [k, v] of Object.entries(patch)) {
      const path = FLAT_TO_PATH[k as keyof CameraSettings];
      if (!path || v === undefined || v === null) continue;
      await apiFetch("/camera/settings", {
        method: "PUT",
        body: { path, value: String(v) },
      });
    }
    return camera.settings();
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
    const raw = await apiFetch<Record<string, any>>("/camera/properties");
    if (Array.isArray(raw)) return raw as CameraProp[];
    // Backend returns a path-keyed dict — flatten to a sorted list.
    return Object.values(raw ?? {})
      .filter((w: any) => w && typeof w === "object" && "path" in w)
      .map((w: any) => ({
        path: String(w.path),
        label: w.label ? String(w.label) : undefined,
        type: w.type ? String(w.type) : undefined,
        value: w.value ?? null,
        choices: Array.isArray(w.choices) ? w.choices.map(String) : null,
        readonly: Boolean(w.readonly),
      })) as CameraProp[];
  },
  async setProperty(path: string, value: string): Promise<void> {
    await apiFetch("/camera/settings", { method: "PUT", body: { path, value } });
  },
};
