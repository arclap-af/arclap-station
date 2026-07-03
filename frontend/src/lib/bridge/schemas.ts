import { z } from "zod";

/**
 * Runtime response validation for the API boundary.
 *
 * The backend is Python — there are no shared types, so a renamed or
 * retyped field silently becomes "—" everywhere in the cockpit and
 * nobody notices until a customer does. These schemas type-check the
 * fields the UI actually depends on and surface drift as a console
 * warning (picked up by the browser log capture the backend ships).
 *
 * Design rule for a control plane: NEVER throw on a live response. The
 * hand-narrowing adapters already degrade gracefully, so validation is
 * advisory — it makes drift *visible* without turning a loose backend
 * response into a broken page. Schemas are therefore lenient (optional /
 * passthrough) and only assert the *type* of a field when it's present,
 * plus the one or two genuinely contractual fields (an id, a kind).
 */

/** Log a warning if `raw` doesn't match `schema`. Never throws. */
export function warnOnDrift(raw: unknown, schema: z.ZodType, label: string): void {
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    // A compact, greppable one-liner — the issues array is what a
    // maintainer needs to see which field drifted.
    console.warn(
      `[arclap] API response drift at ${label}:`,
      parsed.error.issues.map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`).join("; "),
    );
  }
}

const id = z.union([z.string(), z.number()]);

/** Backend destination row (see uploaders/manager to_dict). */
export const destinationSchema = z
  .object({
    id,
    name: z.string().optional(),
    type: z.string().optional(),
    kind: z.string().optional(),
    enabled: z.boolean().optional(),
    config: z.record(z.unknown()).optional(),
    queue_pending: z.number().optional(),
    queue_failed: z.number().optional(),
    last_sync: z.string().nullable().optional(),
    bytes_today: z.number().optional(),
  })
  .passthrough();
export const destinationListSchema = z.array(destinationSchema);

/** Backend schedule row. */
export const scheduleSchema = z
  .object({
    id,
    name: z.string().optional(),
    interval_min: z.number().optional(),
    interval_minutes: z.number().optional(),
    from_time: z.string().optional(),
    to_time: z.string().optional(),
    days: z.array(z.string()).optional(),
    enabled: z.boolean().optional(),
  })
  .passthrough();
export const scheduleListSchema = z.array(scheduleSchema);

/** Backend gallery photo row (PhotoRecord). */
export const galleryPhotoSchema = z
  .object({
    id,
    filename: z.string().optional(),
    captured_at: z.string().optional(),
    size_bytes: z.number().optional(),
    upload_state: z.string().optional(),
  })
  .passthrough();

/** /home telemetry snapshot — the fields the shell + Home actually read. */
export const telemetrySchema = z
  .object({
    hostname: z.string().optional(),
    ip: z.string().optional(),
    status: z.string().optional(),
    uptime_seconds: z.number().optional(),
  })
  .passthrough();
