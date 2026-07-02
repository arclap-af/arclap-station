import { z } from "zod";
import { apiFetch } from "../api";
import { arr, obj } from "./json";

// Frontend-facing shape used by the wizard + the "Acceptance" tab.
export interface AcceptanceResult {
  group: string;
  name: string;
  ok: boolean;
  detail: string | null;
  duration_ms: number;
}
export interface AcceptanceRun {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "pass" | "fail";
  results: AcceptanceResult[];
}

// Loose schemas (backend's actual shape uses different keys; adapter below).
export const acceptanceResultSchema = z.unknown();
export const acceptanceRunSchema = z.unknown();

function adaptResult(raw: Record<string, unknown>): AcceptanceResult {
  // Backend's per-check shape:
  //   {group, check, state: "ok"|"fail"|"skip"|"running", detail, duration_ms}
  const state = String(raw.state ?? "");
  return {
    group: String(raw.group ?? "other"),
    name: String(raw.name ?? raw.check ?? ""),
    ok: state === "ok",
    detail: typeof raw.detail === "string" ? raw.detail : null,
    duration_ms: typeof raw.duration_ms === "number" ? raw.duration_ms : 0,
  };
}

function adaptRun(raw: Record<string, unknown>): AcceptanceRun {
  // Backend's summary shape:
  //   {id, state: "running"|"ok"|"failed", started_at, finished_at,
  //    total, passed, failed, report: [...]}
  const state = String(raw.state ?? "");
  const status: AcceptanceRun["status"] =
    state === "ok"
      ? "pass"
      : state === "failed" || state === "fail"
        ? "fail"
        : "running";
  return {
    run_id: String(raw.id ?? raw.run_id ?? ""),
    started_at: String(raw.started_at ?? ""),
    finished_at: raw.finished_at ? String(raw.finished_at) : null,
    status,
    results: arr(raw.report).map((r) => adaptResult(obj(r))),
  };
}

export const acceptance = {
  async start(): Promise<AcceptanceRun> {
    // Backend route is /api/acceptance/run; setup_wizard's variant only
    // returns {run_id}. Hit the canonical one.
    const raw = await apiFetch<Record<string, unknown>>("/acceptance/run", { method: "POST" });
    // The POST returns {ok, run_id}; we need the full run shape for the UI,
    // so chain into status() right away.
    const runId = String(raw.run_id ?? "");
    if (!runId) {
      return {
        run_id: "",
        started_at: new Date().toISOString(),
        finished_at: null,
        status: "fail",
        results: [],
      };
    }
    return acceptance.status(runId);
  },
  async status(runId: string): Promise<AcceptanceRun> {
    const raw = await apiFetch<Record<string, unknown>>(`/acceptance/status/${runId}`);
    return adaptRun(raw);
  },
};
