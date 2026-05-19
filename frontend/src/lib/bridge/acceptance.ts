import { z } from "zod";
import { apiJson } from "../api";

export const acceptanceResultSchema = z.object({
  group: z.string(),
  name: z.string(),
  ok: z.boolean(),
  detail: z.string().nullable(),
  duration_ms: z.number(),
});
export type AcceptanceResult = z.infer<typeof acceptanceResultSchema>;

export const acceptanceRunSchema = z.object({
  run_id: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  status: z.enum(["running", "pass", "fail"]),
  results: z.array(acceptanceResultSchema),
});
export type AcceptanceRun = z.infer<typeof acceptanceRunSchema>;

export const acceptance = {
  async start(): Promise<AcceptanceRun> {
    return apiJson("/setup/acceptance-run", acceptanceRunSchema, { method: "POST" });
  },
  async status(runId: string): Promise<AcceptanceRun> {
    return apiJson(`/setup/acceptance-run/${runId}`, acceptanceRunSchema);
  },
};
