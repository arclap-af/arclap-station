import { useQuery } from "@tanstack/react-query";

import { settings } from "../../../lib/bridge/settings";

export function Storage() {
  const { data } = useQuery({ queryKey: ["settings.storage"], queryFn: settings.storage });
  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;
  const pct = (data.used_bytes / data.capacity_bytes) * 100;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Disk</div>
        <Row label="Device" val={data.device} mono />
        <Row label="Filesystem" val={data.fs} mono />
        <Row label="Capacity" val={fmtBytes(data.capacity_bytes)} mono />
        <Row label="Used" val={`${fmtBytes(data.used_bytes)} · ${pct.toFixed(1)}%`} mono />
        <Row label="SMART" val={data.smart} />
        <div style={{ height: 8, background: "var(--as-surface-2)", borderRadius: 4, marginTop: 14, overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: pct > 90 ? "var(--as-bad)" : "var(--as-accent)" }} />
        </div>
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Local buffer</div>
        <Row label="Buffer path" val={data.buffer_path} mono />
        <Row label="Max size" val={data.buffer_max} mono />
        <Row label="Retention" val={data.retention} />
        <Row label="When full" val={data.when_full} />
      </div>
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

function fmtBytes(b: number): string {
  if (b >= 1e12) return `${(b / 1e12).toFixed(1)} TB`;
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  return `${b} B`;
}
