import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Toggle } from "../../../components/Toggle";
import { Icon, I } from "../../../components/icons";
import { useToast } from "../../../components/ToastQueue";
import { health, type HealthStatus } from "../../../lib/bridge/health";

const STATUS_META: Record<HealthStatus, { color: string; bg: string; label: string; icon: string }> = {
  ok: { color: "var(--as-accent-2)", bg: "color-mix(in srgb, var(--as-accent-2) 14%, transparent)", label: "Healthy", icon: I.check },
  warn: { color: "var(--as-warn)", bg: "color-mix(in srgb, var(--as-warn) 14%, transparent)", label: "Attention", icon: I.zap },
  bad: { color: "var(--as-bad)", bg: "color-mix(in srgb, var(--as-bad) 14%, transparent)", label: "Problem", icon: I.zap },
  unknown: { color: "var(--as-ink-3)", bg: "var(--as-bg-2)", label: "Unknown", icon: I.clock },
};

export function Health() {
  const qc = useQueryClient();
  const toast = useToast();

  const { data: result, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["health.state"],
    queryFn: health.state,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });

  const runNow = useMutation({
    mutationFn: health.runNow,
    onSuccess: (r) => {
      qc.setQueryData(["health.state"], r);
      toast.show(`Self-test complete · ${r.score}% healthy`, r.overall === "ok" ? "ok" : "warn");
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : "Self-test failed", "bad"),
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const agoSec = dataUpdatedAt ? Math.max(0, Math.floor((now - dataUpdatedAt) / 1000)) : null;

  // ── alerts config ──
  const { data: alerts } = useQuery({ queryKey: ["health.alerts"], queryFn: health.getAlerts });
  const [webhook, setWebhook] = useState("");
  const [hbEnabled, setHbEnabled] = useState(false);
  const [hbInterval, setHbInterval] = useState(60);
  useEffect(() => {
    if (alerts) {
      setWebhook(alerts.alert_webhook ?? "");
      setHbEnabled(alerts.heartbeat_enabled);
      setHbInterval(alerts.heartbeat_interval_min);
    }
  }, [alerts]);

  const saveAlerts = useMutation({
    mutationFn: () =>
      health.updateAlerts({
        alert_webhook: webhook.trim(),
        clear_webhook: webhook.trim() === "",
        heartbeat_enabled: hbEnabled,
        heartbeat_interval_min: hbInterval,
      }),
    onSuccess: (cfg) => {
      qc.setQueryData(["health.alerts"], cfg);
      toast.show("Alert settings saved", "ok");
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : "Save failed", "bad"),
  });

  const testHb = useMutation({
    mutationFn: health.testHeartbeat,
    onSuccess: (r) =>
      toast.show(
        r.ok ? "Heartbeat delivered ✓" : r.configured ? "Webhook configured but delivery failed" : "No webhook configured",
        r.ok ? "ok" : "warn",
      ),
    onError: (e) => toast.show(e instanceof Error ? e.message : "Test failed", "bad"),
  });

  const overall = result?.overall ?? "unknown";
  const meta = STATUS_META[overall];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Overall score header */}
      <div className="as-card" style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div
          style={{
            width: 72, height: 72, borderRadius: 16, flexShrink: 0,
            display: "grid", placeItems: "center",
            background: meta.bg, color: meta.color,
            border: `1px solid ${meta.color}`,
          }}
        >
          <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1 }}>{result?.score ?? "—"}</div>
          <div style={{ fontSize: 9, opacity: 0.8 }}>/100</div>
        </div>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: meta.color }}>{meta.label}</div>
          <div style={{ fontSize: 12.5, color: "var(--as-ink-3)", marginTop: 2 }}>
            {result
              ? `${result.checks.filter((c) => c.status === "ok").length}/${result.checks.length} checks passing`
              : "Running self-test…"}
            {agoSec !== null && <span> · updated {isFetching ? "now" : `${agoSec}s ago`}</span>}
          </div>
        </div>
        <Button variant="primary" onClick={() => runNow.mutate()} disabled={runNow.isPending} style={{ padding: "9px 16px" }}>
          <Icon d={I.refresh} size={14} /> {runNow.isPending ? "Running…" : "Run self-test"}
        </Button>
      </div>

      {/* Per-check list */}
      <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--as-line)", fontSize: 13, fontWeight: 700 }}>
          Checks
        </div>
        <div>
          {(result?.checks ?? []).map((c) => {
            const m = STATUS_META[c.status];
            return (
              <div key={c.id} style={{ display: "flex", gap: 12, padding: "12px 16px", borderBottom: "1px solid var(--as-line)", alignItems: "flex-start" }}>
                <div
                  style={{
                    width: 26, height: 26, borderRadius: 7, flexShrink: 0,
                    display: "grid", placeItems: "center",
                    background: m.bg, color: m.color, marginTop: 1,
                  }}
                >
                  <Icon d={m.icon} size={14} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                    <span style={{ fontSize: 13.5, fontWeight: 600 }}>{c.label}</span>
                    <span style={{ fontSize: 12, color: "var(--as-ink-3)" }}>{c.detail}</span>
                  </div>
                  {c.hint && c.status !== "ok" && (
                    <div style={{ fontSize: 11.5, color: m.color, marginTop: 4, lineHeight: 1.5 }}>{c.hint}</div>
                  )}
                </div>
              </div>
            );
          })}
          {!result && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--as-ink-3)" }}>Running self-test…</div>
          )}
        </div>
      </div>

      {/* Alerts + heartbeat config */}
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Alerts &amp; heartbeat</div>
        <div style={{ fontSize: 12, color: "var(--as-ink-3)", marginBottom: 14, lineHeight: 1.5 }}>
          Point this at a Slack / Teams / Make.com / custom webhook. The station POSTs an alert when its
          health degrades or recovers, and — if heartbeat is on — a periodic "alive + summary" so a
          silent or dead station is detectable from the fleet side.
        </div>

        <label style={{ display: "block", fontSize: 12, color: "var(--as-ink-3)", marginBottom: 5 }}>Alert webhook URL</label>
        <input
          className="as-input mono"
          placeholder="https://hooks.example.com/…"
          value={webhook}
          onChange={(e) => setWebhook(e.target.value)}
          style={{ marginBottom: 14 }}
        />

        <div className="as-stat-row">
          <span className="as-stat-label">Send periodic heartbeat</span>
          <Toggle on={hbEnabled} onChange={setHbEnabled} />
        </div>
        <div className="as-stat-row">
          <span className="as-stat-label">Heartbeat interval</span>
          <select
            className="as-select"
            value={hbInterval}
            onChange={(e) => setHbInterval(parseInt(e.target.value, 10))}
            style={{ width: 130 }}
            disabled={!hbEnabled}
          >
            <option value={15}>Every 15 min</option>
            <option value={30}>Every 30 min</option>
            <option value={60}>Hourly</option>
            <option value={360}>Every 6 h</option>
            <option value={720}>Every 12 h</option>
            <option value={1440}>Daily</option>
          </select>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <Button variant="primary" onClick={() => saveAlerts.mutate()} disabled={saveAlerts.isPending}>
            {saveAlerts.isPending ? "Saving…" : "Save"}
          </Button>
          <Button onClick={() => testHb.mutate()} disabled={testHb.isPending || !webhook.trim()}>
            {testHb.isPending ? "Sending…" : "Send test heartbeat"}
          </Button>
        </div>
      </div>
    </div>
  );
}
