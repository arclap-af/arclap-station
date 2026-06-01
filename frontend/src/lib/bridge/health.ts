import { z } from "zod";

import { apiFetch, apiJson } from "../api";

export type HealthStatus = "ok" | "warn" | "bad" | "unknown";

export interface HealthCheck {
  id: string;
  label: string;
  status: HealthStatus;
  detail: string;
  hint: string | null;
}

export interface HealthResult {
  overall: HealthStatus;
  score: number;
  ran_at: string;
  checks: HealthCheck[];
}

export interface AlertConfig {
  alert_webhook: string | null;
  heartbeat_enabled: boolean;
  heartbeat_interval_min: number;
}

const resultSchema = z.record(z.unknown());

function adaptResult(raw: Record<string, any>): HealthResult {
  const checks = Array.isArray(raw.checks) ? raw.checks : [];
  return {
    overall: (["ok", "warn", "bad", "unknown"].includes(raw.overall) ? raw.overall : "unknown") as HealthStatus,
    score: typeof raw.score === "number" ? raw.score : 0,
    ran_at: String(raw.ran_at ?? ""),
    checks: checks.map((c: any) => ({
      id: String(c.id ?? ""),
      label: String(c.label ?? ""),
      status: (["ok", "warn", "bad", "unknown"].includes(c.status) ? c.status : "unknown") as HealthStatus,
      detail: String(c.detail ?? ""),
      hint: c.hint ? String(c.hint) : null,
    })),
  };
}

export const health = {
  /** Last persisted self-test result (cheap; for polling). */
  async state(): Promise<HealthResult> {
    const raw = await apiJson("/health/state", resultSchema);
    return adaptResult(raw as Record<string, any>);
  },
  /** Force a fresh self-test run. */
  async runNow(): Promise<HealthResult> {
    const raw = await apiJson("/health/selftest", resultSchema);
    return adaptResult(raw as Record<string, any>);
  },
  async getAlerts(): Promise<AlertConfig> {
    const raw = (await apiJson("/settings/alerts", z.record(z.unknown()))) as any;
    return {
      alert_webhook: raw.alert_webhook ?? null,
      heartbeat_enabled: Boolean(raw.heartbeat_enabled),
      heartbeat_interval_min: Number(raw.heartbeat_interval_min ?? 60),
    };
  },
  async updateAlerts(body: {
    alert_webhook?: string;
    clear_webhook?: boolean;
    heartbeat_enabled?: boolean;
    heartbeat_interval_min?: number;
  }): Promise<AlertConfig> {
    const raw = (await apiJson("/settings/alerts", z.record(z.unknown()), {
      method: "PUT",
      body,
    })) as any;
    return {
      alert_webhook: raw.alert_webhook ?? null,
      heartbeat_enabled: Boolean(raw.heartbeat_enabled),
      heartbeat_interval_min: Number(raw.heartbeat_interval_min ?? 60),
    };
  },
  async testHeartbeat(): Promise<{ ok: boolean; configured: boolean }> {
    const raw = (await apiFetch<any>("/health/heartbeat/test", { method: "POST" })) ?? {};
    return { ok: Boolean(raw.ok), configured: Boolean(raw.configured) };
  },
};
