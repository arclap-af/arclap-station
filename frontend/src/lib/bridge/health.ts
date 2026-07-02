import { z } from "zod";

import { apiFetch, apiJson } from "../api";
import { arr, obj, strOrNull } from "./json";

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

const STATUSES = ["ok", "warn", "bad", "unknown"];
function asStatus(v: unknown): HealthStatus {
  return (STATUSES.includes(String(v)) ? String(v) : "unknown") as HealthStatus;
}

function adaptResult(raw: Record<string, unknown>): HealthResult {
  return {
    overall: asStatus(raw.overall),
    score: typeof raw.score === "number" ? raw.score : 0,
    ran_at: String(raw.ran_at ?? ""),
    checks: arr(raw.checks).map((entry) => {
      const c = obj(entry);
      return {
        id: String(c.id ?? ""),
        label: String(c.label ?? ""),
        status: asStatus(c.status),
        detail: String(c.detail ?? ""),
        hint: c.hint ? String(c.hint) : null,
      };
    }),
  };
}

export const health = {
  /** Last persisted self-test result (cheap; for polling). */
  async state(): Promise<HealthResult> {
    const raw = await apiJson("/health/state", resultSchema);
    return adaptResult(raw);
  },
  /** Force a fresh self-test run. */
  async runNow(): Promise<HealthResult> {
    const raw = await apiJson("/health/selftest", resultSchema);
    return adaptResult(raw);
  },
  async getAlerts(): Promise<AlertConfig> {
    const raw = await apiJson("/settings/alerts", z.record(z.unknown()));
    return {
      alert_webhook: strOrNull(raw.alert_webhook),
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
    const raw = await apiJson("/settings/alerts", z.record(z.unknown()), {
      method: "PUT",
      body,
    });
    return {
      alert_webhook: strOrNull(raw.alert_webhook),
      heartbeat_enabled: Boolean(raw.heartbeat_enabled),
      heartbeat_interval_min: Number(raw.heartbeat_interval_min ?? 60),
    };
  },
  async testHeartbeat(): Promise<{ ok: boolean; configured: boolean }> {
    const raw = obj(await apiFetch<Record<string, unknown>>("/health/heartbeat/test", { method: "POST" }));
    return { ok: Boolean(raw.ok), configured: Boolean(raw.configured) };
  },
};
