import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const destinationKind = z.enum(["s3", "sftp", "ftp", "webhook", "local", "mqtt", "arc"]);
export type DestinationKind = z.infer<typeof destinationKind>;

export const destinationSchema = z.object({
  id: z.string(),
  kind: destinationKind,
  name: z.string(),
  enabled: z.boolean(),
  config: z.record(z.unknown()),
  queue_pending: z.number().int(),
  queue_failed: z.number().int(),
  last_sync: z.string().nullable(),
  bytes_today: z.number().int(),
  retry_policy: z.number().int(),
  encrypt_in_transit: z.boolean(),
});
export type Destination = z.infer<typeof destinationSchema>;

export type DestinationDraft = Omit<Destination, "id" | "queue_pending" | "queue_failed" | "last_sync" | "bytes_today"> & {
  id?: string;
};

export const destinationTestSchema = z.object({
  ok: z.boolean(),
  steps: z.array(
    z.object({
      label: z.string(),
      ok: z.boolean(),
      detail: z.string().nullable(),
    }),
  ),
});
export type DestinationTest = z.infer<typeof destinationTestSchema>;

export const destinations = {
  async list(): Promise<Destination[]> {
    return apiJson("/destinations", z.array(destinationSchema));
  },
  async test(payload: DestinationDraft): Promise<DestinationTest> {
    return apiJson("/destinations/test", destinationTestSchema, { method: "POST", body: payload });
  },
  async create(payload: DestinationDraft): Promise<Destination> {
    return apiJson("/destinations", destinationSchema, { method: "POST", body: payload });
  },
  async update(id: string, payload: DestinationDraft): Promise<Destination> {
    return apiJson(`/destinations/${id}`, destinationSchema, { method: "PUT", body: payload });
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/destinations/${id}`, { method: "DELETE" });
  },
  async setEnabled(id: string, enabled: boolean): Promise<Destination> {
    return apiJson(`/destinations/${id}/enabled`, destinationSchema, { method: "POST", body: { enabled } });
  },
};
