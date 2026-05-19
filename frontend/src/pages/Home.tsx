import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Icon, I } from "../components/icons";
import { home as homeApi, adaptTelemetry, type ActivityEvent, type Telemetry } from "../lib/bridge/home";
import { useWebSocket } from "../lib/ws";

export function Home() {
  const [live, setLive] = useState<Telemetry | null>(null);

  const { data: telemetry, refetch } = useQuery({
    queryKey: ["home.telemetry"],
    queryFn: homeApi.telemetry,
    refetchInterval: 30_000,
  });
  const { data: activity } = useQuery({
    queryKey: ["home.activity"],
    queryFn: () => homeApi.activity(10),
    refetchInterval: 15_000,
  });

  const onWsMessage = useCallback((ev: MessageEvent) => {
    try {
      // WS frames carry the raw backend snapshot (the same shape
      // /api/home returns); pipe it through the same adapter the
      // polled query uses so the field names match what the UI reads.
      const raw = JSON.parse(ev.data) as Record<string, unknown>;
      setLive(adaptTelemetry(raw));
    } catch {
      // ignore non-JSON frames or shape errors — fall back to polled data
    }
  }, []);
  const { status: wsStatus } = useWebSocket("/api/home/ws", onWsMessage);
  const t = live ?? telemetry;

  if (!t) {
    return (
      <div className="as-scroll">
        <div className="as-page">
          <div className="as-h1">Station overview</div>
          <div className="as-h1-sub">Reading telemetry…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1200 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 16 }}>
          <div>
            <h1 className="as-h1">Station overview</h1>
            <div className="as-h1-sub">
              {t.hostname} · {t.ip} · v{t.firmware} · uptime {fmtUptime(t.uptime_seconds)}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill tone={wsStatus === "open" ? "ok" : wsStatus === "connecting" ? "warn" : "gray"}>
              {wsStatus === "open" ? "Live" : wsStatus === "connecting" ? "Connecting" : "Polled"}
            </Pill>
            <Button style={{ padding: "6px 12px", fontSize: 12 }} onClick={() => refetch()}>
              <Icon d={I.refresh} size={13} /> Refresh
            </Button>
          </div>
        </div>

        <div className="as-grid-4" style={{ marginBottom: 14 }}>
          <Stat label="Status" val={t.status === "online" ? "Online" : t.status === "warn" ? "Warning" : "Offline"} sub={`Last sync ${t.last_sync_seconds_ago}s`} color="var(--as-accent-2)" />
          <Stat label="Captures today" val={String(t.captures_today)} sub={t.next_capture_seconds !== null ? `Next in ${Math.round(t.next_capture_seconds / 60)} min` : "No active schedule"} />
          <Stat label="Queue" val={String(t.queue_pending)} sub={`Avg upload ${t.avg_upload_seconds.toFixed(1)}s`} />
          <Stat label="Storage" val={`${Math.round(t.storage_used_pct)}%`} sub={`${fmtBytes(t.storage_free_bytes)} free`} />
        </div>
        <div className="as-grid-4" style={{ marginBottom: 18 }}>
          <Stat label="CPU" val={`${Math.round(t.cpu_pct)}%`} sub={`${t.cpu_temp_c.toFixed(1)}°C`} />
          <Stat label="Memory" val={`${Math.round(t.memory_used_mb)} MB`} sub={`of ${Math.round(t.memory_total_mb)} MB`} />
          <Stat label="Network" val={`${t.network_throughput_mbps.toFixed(1)} Mbps`} sub={t.network_signal_dbm !== null ? `Wi-Fi · ${t.network_signal_dbm} dBm` : "Wired"} />
          <Stat label="UPS" val={t.ups_pct !== null ? `${t.ups_pct}%` : "—"} sub={t.ups_status ?? "no UPS"} />
        </div>

        <div className="as-grid-2">
          <div className="as-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Camera</div>
              <Pill tone={t.camera?.detected ? "ok" : "warn"}>
                {t.camera?.detected ? "Detected" : "Not detected"}
              </Pill>
            </div>
            <Row label="Model" val={t.camera?.model ?? "—"} />
            <Row label="Lens" val={t.camera?.lens ?? "—"} />
            <Row label="Battery" val={t.camera?.battery_pct !== null && t.camera?.battery_pct !== undefined ? `${t.camera.battery_pct}%` : "—"} mono />
            <Row label="Shutter count" val={t.camera?.shutter_count?.toLocaleString() ?? "—"} mono />
            <Row label="Sensor temp" val={t.camera?.sensor_temp_c !== null && t.camera?.sensor_temp_c !== undefined ? `${t.camera.sensor_temp_c}°C` : "—"} mono />
            <Row label="USB port" val={t.camera?.usb_port ?? "—"} mono />
            <Row label="Driver" val={t.camera?.driver ?? "—"} mono />
            <Link to="/camera" style={{ textDecoration: "none" }}>
              <Button variant="primary" style={{ width: "100%", marginTop: 14 }}>
                Open camera
              </Button>
            </Link>
          </div>

          <div className="as-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Quick links</div>
              <Pill tone="gray">{t.firmware}</Pill>
            </div>
            <Row label="Pending uploads" val={String(t.queue_pending)} mono />
            <Row label="Failed uploads" val={String(t.queue_failed)} mono />
            <Link to="/gallery" style={{ textDecoration: "none" }}>
              <Button style={{ width: "100%", marginTop: 14 }}>Open gallery</Button>
            </Link>
            <Link to="/schedule" style={{ textDecoration: "none" }}>
              <Button style={{ width: "100%", marginTop: 8 }}>Manage schedule</Button>
            </Link>
            <Link to="/destinations" style={{ textDecoration: "none" }}>
              <Button style={{ width: "100%", marginTop: 8 }}>Manage destinations</Button>
            </Link>
          </div>
        </div>

        <div className="as-card" style={{ padding: 0, marginTop: 14, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>Recent activity</div>
            <span style={{ fontSize: 11, color: "var(--as-ink-3)", fontFamily: "var(--as-mono)" }}>last {activity?.length ?? 0} events · journalctl</span>
          </div>
          <div style={{ fontFamily: "var(--as-mono)", fontSize: 11.5, padding: 8, lineHeight: 1.6 }}>
            {(activity ?? []).map((a: ActivityEvent, i) => (
              <div key={i} style={{ display: "flex", gap: 10, padding: "3px 8px" }}>
                <span style={{ color: "var(--as-ink-4)" }}>{a.ts}</span>
                <span style={{ color: "var(--as-ink-3)", width: 90, flexShrink: 0 }}>{a.service}</span>
                <span
                  style={{
                    color: a.level === "error" ? "var(--as-bad)" : a.level === "warn" ? "var(--as-warn)" : "var(--as-accent-2)",
                    width: 48,
                    flexShrink: 0,
                    fontWeight: 700,
                  }}
                >
                  {a.level}
                </span>
                <span style={{ color: "var(--as-ink-2)" }}>{a.message}</span>
              </div>
            ))}
            {(activity?.length ?? 0) === 0 && (
              <div style={{ padding: "16px", color: "var(--as-ink-3)" }}>No recent events.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, val, sub, color }: { label: string; val: string; sub: string; color?: string }) {
  return (
    <div className="as-card">
      <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, marginTop: 6, color: color ?? "var(--as-ink)" }}>{val}</div>
      <div style={{ fontSize: 12, color: "var(--as-ink-3)", marginTop: 2 }}>{sub}</div>
    </div>
  );
}

function Row({ label, val, mono }: { label: string; val: string; mono?: boolean }) {
  return (
    <div className="as-stat-row">
      <span className="as-stat-label">{label}</span>
      <span className={`as-stat-val${mono ? " mono" : ""}`}>{val}</span>
    </div>
  );
}

function fmtUptime(seconds: number): string {
  if (!seconds) return "—";
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtBytes(b: number): string {
  if (b >= 1e12) return `${(b / 1e12).toFixed(1)} TB`;
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  if (b >= 1e3) return `${(b / 1e3).toFixed(1)} KB`;
  return `${b} B`;
}
