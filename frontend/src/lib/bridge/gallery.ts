import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const photoSchema = z.object({
  id: z.string(),
  filename: z.string(),
  captured_at: z.string(),
  size_bytes: z.number().int(),
  width: z.number().int(),
  height: z.number().int(),
  iso: z.string(),
  shutter: z.string(),
  aperture: z.string(),
  starred: z.boolean(),
  uploads: z.array(
    z.object({
      destination_id: z.string(),
      destination_name: z.string(),
      state: z.enum(["pending", "in_progress", "uploaded", "failed"]),
      uploaded_at: z.string().nullable(),
      remote_key: z.string().nullable(),
    }),
  ),
  path: z.string(),
  thumb_url: z.string(),
  original_url: z.string(),
});
export type Photo = z.infer<typeof photoSchema>;

export const gallery = {
  async list(params?: { filter?: "all" | "uploaded" | "pending" | "starred"; query?: string }): Promise<Photo[]> {
    const qs = new URLSearchParams();
    if (params?.filter) qs.set("filter", params.filter);
    if (params?.query) qs.set("q", params.query);
    const path = `/gallery${qs.toString() ? `?${qs}` : ""}`;
    return apiJson(path, z.array(photoSchema));
  },
  async star(id: string, starred: boolean): Promise<void> {
    await apiFetch(`/gallery/${id}/star`, { method: "POST", body: { starred } });
  },
  async retry(id: string, destinationId: string): Promise<void> {
    await apiFetch(`/gallery/${id}/retry`, { method: "POST", body: { destination_id: destinationId } });
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/gallery/${id}`, { method: "DELETE" });
  },
};
