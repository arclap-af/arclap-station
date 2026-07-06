import { z } from "zod";
import { apiFetch, apiJson, API_PREFIX } from "../api";
import { obj } from "./json";
import { galleryPhotoSchema, warnOnDrift } from "./schemas";

// What the Gallery page renders. Built from the backend's PhotoRecord
// in /api/gallery/list, with sensible fallbacks for fields the backend
// doesn't currently produce (iso, shutter, aperture, starred, uploads
// per-destination details).
export interface Photo {
  id: string;
  filename: string;
  captured_at: string;
  size_bytes: number;
  width: number;
  height: number;
  iso: string;
  shutter: string;
  aperture: string;
  starred: boolean;
  uploads: Array<{
    destination_id: string;
    destination_name: string;
    state: "pending" | "in_progress" | "uploaded" | "failed";
    uploaded_at: string | null;
    remote_key: string | null;
  }>;
  path: string;
  thumb_url: string;
  original_url: string;
}

// Kept as a permissive schema since backend trims fields.
export const photoSchema = z.unknown();

const listResponseSchema = z.object({
  total: z.number().int().nonnegative(),
  items: z.array(z.record(z.unknown())),
});

function adaptPhoto(raw: Record<string, unknown>): Photo {
  const id = String(raw.id ?? "");
  const exif = obj(raw.exif);
  // Backend currently returns a single `upload_state` string per photo,
  // not a per-destination array. Synthesize one entry so the UI's
  // Uploaded/Pending pill stops permanently saying "Local".
  let uploads: Photo["uploads"];
  if (Array.isArray(raw.uploads) && raw.uploads.length > 0) {
    uploads = raw.uploads;
  } else if (raw.upload_state) {
    const state = String(raw.upload_state);
    const mappedState: "pending" | "in_progress" | "uploaded" | "failed" =
      state === "done"
        ? "uploaded"
        : state === "in_progress"
          ? "in_progress"
          : state === "failed" || state === "failed_permanent"
            ? "failed"
            : "pending";
    uploads = [
      {
        destination_id: "any",
        destination_name: "Destinations",
        state: mappedState,
        uploaded_at: mappedState === "uploaded" ? String(raw.captured_at ?? "") : null,
        remote_key: null,
      },
    ];
  } else {
    uploads = [];
  }
  return {
    id,
    filename: String(raw.filename ?? "unknown.jpg"),
    captured_at: String(raw.captured_at ?? raw.created_at ?? ""),
    size_bytes: typeof raw.size_bytes === "number" ? raw.size_bytes : 0,
    width: typeof raw.width === "number" ? raw.width : 0,
    height: typeof raw.height === "number" ? raw.height : 0,
    iso: exif.iso ? String(exif.iso) : "—",
    shutter: exif.shutter ? String(exif.shutter) : "—",
    aperture: exif.aperture ? String(exif.aperture) : "—",
    starred: Boolean(raw.starred),
    uploads,
    path: String(raw.path ?? ""),
    thumb_url: `${API_PREFIX}/gallery/${id}/thumb`,
    original_url: `${API_PREFIX}/gallery/${id}/full`,
  };
}

export const gallery = {
  async list(params?: {
    filter?: "all" | "uploaded" | "pending" | "starred";
    query?: string;
  }): Promise<Photo[]> {
    const qs = new URLSearchParams();
    if (params?.filter && params.filter !== "all") qs.set("filter", params.filter);
    if (params?.query) qs.set("q", params.query);
    const path = `/gallery/list${qs.toString() ? `?${qs}` : ""}`;
    const resp = await apiJson(path, listResponseSchema);
    warnOnDrift(resp.items, z.array(galleryPhotoSchema), "gallery.list");
    return resp.items.map(adaptPhoto);
  },
  async listPage(params: {
    filter?: "all" | "uploaded" | "pending" | "starred";
    query?: string;
    limit: number;
    offset: number;
  }): Promise<{ items: Photo[]; total: number }> {
    const qs = new URLSearchParams();
    if (params.filter && params.filter !== "all") qs.set("filter", params.filter);
    if (params.query) qs.set("q", params.query);
    qs.set("limit", String(params.limit));
    qs.set("offset", String(params.offset));
    const resp = await apiJson(`/gallery/list?${qs}`, listResponseSchema);
    warnOnDrift(resp.items, z.array(galleryPhotoSchema), "gallery.listPage");
    return { items: resp.items.map(adaptPhoto), total: resp.total };
  },
  async bulkDelete(payload: {
    ids?: string[];
    all?: boolean;
    filter?: string;
    query?: string;
  }): Promise<number> {
    const body: Record<string, unknown> = {};
    if (payload.all) {
      body.all = true;
      if (payload.filter && payload.filter !== "all") body.filter = payload.filter;
      if (payload.query) body.query = payload.query;
    } else {
      body.ids = (payload.ids ?? []).map((x) => Number(x));
    }
    const raw = (await apiJson("/gallery/bulk-delete", z.unknown(), {
      method: "POST",
      body,
    })) as { deleted?: number };
    return Number(raw?.deleted ?? 0);
  },
  /**
   * URL that streams every matching photo as one ZIP (each entry named
   * with its capture timestamp). Same filter/search as the list, so it
   * matches what the operator sees. The browser downloads it directly
   * (the server sets the Content-Disposition filename); the session
   * cookie rides along automatically on the same-origin request.
   */
  downloadAllUrl(params?: { filter?: string; query?: string }): string {
    const qs = new URLSearchParams();
    if (params?.filter && params.filter !== "all") qs.set("filter", params.filter);
    if (params?.query) qs.set("q", params.query);
    const s = qs.toString();
    return `${API_PREFIX}/gallery/download-all${s ? `?${s}` : ""}`;
  },
  async star(id: string, starred: boolean): Promise<void> {
    await apiFetch(`/gallery/${id}/star`, { method: "POST", body: { starred } });
  },
  async retry(id: string, _destinationId: string): Promise<void> {
    // Requeue this photo's failed/permanently-failed uploads, then the
    // worker picks them up. The recovery path that used to be missing.
    await apiFetch(`/queue/retry-failed?photo_id=${encodeURIComponent(id)}`, { method: "POST" });
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/gallery/${id}`, { method: "DELETE" });
  },
};
