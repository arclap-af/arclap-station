import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const destinationKind = z.enum(["s3", "sftp", "ftp", "webhook", "local", "mqtt", "arc"]);
export type DestinationKind = z.infer<typeof destinationKind>;

export interface Destination {
  id: string;
  kind: DestinationKind;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  queue_pending: number;
  queue_failed: number;
  last_sync: string | null;
  bytes_today: number;
  retry_policy: number;
  encrypt_in_transit: boolean;
}
export type DestinationDraft = Omit<
  Destination,
  "id" | "queue_pending" | "queue_failed" | "last_sync" | "bytes_today"
> & { id?: string };

export const destinationSchema = z.unknown();
export const destinationTestSchema = z.unknown();
export type DestinationTest = {
  ok: boolean;
  steps: Array<{ label: string; ok: boolean; detail: string | null }>;
};

function adapt(raw: Record<string, any>): Destination {
  const kind = (raw.type ?? raw.kind ?? "local") as DestinationKind;
  return {
    id: String(raw.id ?? ""),
    kind,
    name: String(raw.name ?? "Destination"),
    enabled: Boolean(raw.enabled),
    config: (raw.config ?? {}) as Record<string, unknown>,
    queue_pending: Number(raw.queue_pending ?? 0),
    queue_failed: Number(raw.queue_failed ?? raw.last_error ? 1 : 0),
    last_sync: typeof raw.last_sync === "string" ? raw.last_sync : null,
    bytes_today: Number(raw.bytes_today ?? 0),
    retry_policy: Number(raw.retry_policy ?? 3),
    encrypt_in_transit: Boolean(raw.encrypt_in_transit ?? true),
  };
}

function toBackend(p: DestinationDraft): Record<string, unknown> {
  return {
    name: p.name,
    type: p.kind,
    config: p.config,
    enabled: p.enabled,
  };
}

export const destinations = {
  async list(): Promise<Destination[]> {
    const raw = await apiJson("/destinations/list", z.array(z.record(z.unknown())));
    return raw.map(adapt);
  },
  async test(payload: DestinationDraft): Promise<DestinationTest> {
    try {
      const raw = (await apiJson("/destinations/test", z.unknown(), {
        method: "POST",
        body: toBackend(payload),
      })) as any;
      // Backend returns the uploader test result directly; wrap into steps[].
      return {
        ok: Boolean(raw?.ok ?? true),
        steps: [
          {
            label: payload.name || "Test",
            ok: Boolean(raw?.ok ?? true),
            detail: raw?.detail ?? null,
          },
        ],
      };
    } catch (e) {
      return {
        ok: false,
        steps: [
          {
            label: payload.name || "Test",
            ok: false,
            detail: e instanceof Error ? e.message : String(e),
          },
        ],
      };
    }
  },
  async create(payload: DestinationDraft): Promise<Destination> {
    const raw = await apiJson("/destinations/create", z.record(z.unknown()), {
      method: "POST",
      body: toBackend(payload),
    });
    return adapt(raw);
  },
  async update(id: string, payload: DestinationDraft): Promise<Destination> {
    const raw = await apiJson(`/destinations/${id}`, z.record(z.unknown()), {
      method: "PUT",
      body: toBackend(payload),
    });
    return adapt(raw);
  },
  async remove(id: string): Promise<void> {
    await apiFetch(`/destinations/${id}`, { method: "DELETE" });
  },
  async setEnabled(id: string, enabled: boolean): Promise<Destination> {
    // No per-flag toggle on backend; do a PUT with just enabled.
    const raw = await apiJson(`/destinations/${id}`, z.record(z.unknown()), {
      method: "PUT",
      body: { enabled },
    });
    return adapt(raw);
  },
};
