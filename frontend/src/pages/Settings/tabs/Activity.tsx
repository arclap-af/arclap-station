import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { apiJson } from "../../../lib/api";
import { z } from "zod";

// Audit events from the backend's audit_log table. Every meaningful
// action on the station emits one of these per CLAUDE.md §12.10 —
// user clicks, schedules firing, uploads succeeding / failing,
// destinations created / edited, service restarts, etc. The cockpit
// surfaces them here so the operator gets a single timeline of
// "what happened on this station" without needing to read journald.
interface AuditEvent {
  id: number;
  ts: string;
  actor: string;
  event: string;
  details: Record<string, unknown> | string | null;
  hash: string | null;
}

const auditSchema = z.array(z.record(z.unknown()));

async function fetchActivity(limit: number): Promise<AuditEvent[]> {
  const raw = await apiJson(`/settings/audit/recent?limit=${limit}`, auditSchema);
  return raw.map((r: any) => ({
    id: Number(r.id ?? 0),
    ts: String(r.ts ?? ""),
    actor: String(r.actor ?? "system"),
    event: String(r.event ?? ""),
    details: (r.details ?? null) as AuditEvent["details"],
    hash: r.hash ? String(r.hash).slice(0, 12) : null,
  }));
}

// Group events by an "icon" + colour based on the event prefix so the
// timeline reads at a glance — captures green, uploads green,
// failures red, system grey, user blue.
function eventStyle(ev: string): { color: string; bg: string; icon: string } {
  if (ev.startsWith("capture")) return { color: "var(--as-accent-2)", bg: "color-mix(in srgb, var(--as-accent-2) 14%, transparent)", icon: "📸" };
  if (ev.startsWith("upload.fail")) return { color: "var(--as-bad)", bg: "color-mix(in srgb, var(--as-bad) 14%, transparent)", icon: "⚠" };
  if (ev.startsWith("upload")) return { color: "var(--as-accent-2)", bg: "color-mix(in srgb, var(--as-accent-2) 14%, transparent)", icon: "↑" };
  if (ev.startsWith("destination")) return { color: "var(--as-ink)", bg: "var(--as-bg-2)", icon: "⤳" };
  if (ev.startsWith("schedule")) return { color: "var(--as-ink)", bg: "var(--as-bg-2)", icon: "⏱" };
  if (ev.startsWith("camera")) return { color: "var(--as-accent-2)", bg: "color-mix(in srgb, var(--as-accent-2) 14%, transparent)", icon: "◉" };
  if (ev.startsWith("auth")) return { color: "var(--as-warn)", bg: "color-mix(in srgb, var(--as-warn) 14%, transparent)", icon: "🔑" };
  if (ev.startsWith("system") || ev.startsWith("service")) return { color: "var(--as-ink-3)", bg: "var(--as-bg-2)", icon: "⚙" };
  return { color: "var(--as-ink-2)", bg: "var(--as-bg-2)", icon: "•" };
}

const FILTERS: Array<{ id: string; label: string; match: (ev: string) => boolean }> = [
  { id: "all", label: "All", match: () => true },
  { id: "captures", label: "Captures", match: (ev) => ev.startsWith("capture") },
  { id: "uploads", label: "Uploads", match: (ev) => ev.startsWith("upload") },
  { id: "user", label: "User actions", match: (ev) => ev.startsWith("destination") || ev.startsWith("schedule") || ev.startsWith("camera") || ev.startsWith("auth") },
  { id: "errors", label: "Errors", match: (ev) => ev.includes("fail") || ev.includes("error") || ev.includes("orphan") },
];

function fmtTs(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return iso;
  }
}

function fmtDetails(details: AuditEvent["details"]): string {
  if (details === null || details === undefined) return "";
  if (typeof details === "string") return details;
  // Compact one-liner of the details object — keeps the row scannable.
  const parts: string[] = [];
  for (const [k, v] of Object.entries(details)) {
    if (v === null || v === undefined) continue;
    const value = typeof v === "object" ? JSON.stringify(v) : String(v);
    parts.push(`${k}=${value.length > 60 ? value.slice(0, 60) + "…" : value}`);
  }
  return parts.join(" · ");
}

export function Activity() {
  const [limit, setLimit] = useState(200);
  const [filterId, setFilterId] = useState<string>("all");
  const [query, setQuery] = useState("");

  const {
    data: events = [],
    refetch,
    isFetching,
    dataUpdatedAt,
  } = useQuery({
    queryKey: ["settings.activity", limit],
    queryFn: () => fetchActivity(limit),
    // Auto-refresh so a fresh capture / upload event shows up
    // without the operator hitting refresh manually.
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const updatedAgoSec = dataUpdatedAt
    ? Math.max(0, Math.floor((now - dataUpdatedAt) / 1000))
    : null;

  const filter = FILTERS.find((f) => f.id === filterId) ?? FILTERS[0];
  const filtered = events.filter((e) => {
    if (!filter.match(e.event)) return false;
    if (query) {
      const q = query.toLowerCase();
      const haystack = `${e.event} ${e.actor} ${fmtDetails(e.details)}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--as-line)",
          background: "var(--as-bg-2)",
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700 }}>Activity</div>
        <input
          className="as-input"
          placeholder="Search events…"
          style={{ flex: "1 1 200px", height: 30, fontSize: 12 }}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div style={{ display: "flex", border: "1px solid var(--as-line)", borderRadius: 8, overflow: "hidden" }}>
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilterId(f.id)}
              style={{
                padding: "6px 10px",
                border: "none",
                background: filterId === f.id ? "var(--as-accent-soft)" : "transparent",
                color: filterId === f.id ? "var(--as-accent-2)" : "var(--as-ink-2)",
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "inherit",
                borderRight: f.id === FILTERS[FILTERS.length - 1].id ? "none" : "1px solid var(--as-line)",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <select
          className="as-select"
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          style={{ width: 90, height: 30, fontSize: 12 }}
          aria-label="Page size"
        >
          {[50, 100, 200, 500, 1000].map((n) => (
            <option key={n} value={n}>{n} rows</option>
          ))}
        </select>
        <span
          style={{
            fontSize: 11,
            color: "var(--as-ink-3)",
            fontFamily: "var(--as-mono)",
            minWidth: 110,
            textAlign: "right",
          }}
        >
          {isFetching
            ? "refreshing…"
            : updatedAgoSec === null
              ? "—"
              : updatedAgoSec < 2
                ? "just now"
                : `updated ${updatedAgoSec}s ago`}
        </span>
        <Button style={{ padding: "6px 10px", fontSize: 12 }} onClick={() => refetch()} disabled={isFetching}>
          Refresh
        </Button>
      </div>

      <div
        style={{
          padding: 10,
          maxHeight: 640,
          overflowY: "auto",
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: "var(--as-ink-3)",
            padding: "0 6px 8px",
          }}
        >
          {filtered.length} {filtered.length === 1 ? "event" : "events"} · newest first · auto-refresh 5 s
        </div>

        {filtered.map((e) => {
          const style = eventStyle(e.event);
          const det = fmtDetails(e.details);
          return (
            <div
              key={e.id}
              style={{
                display: "flex",
                gap: 10,
                padding: "8px 10px",
                marginBottom: 4,
                borderRadius: 8,
                background: style.bg,
                fontFamily: "var(--as-mono)",
                fontSize: 11.5,
                alignItems: "flex-start",
              }}
            >
              <span
                style={{
                  fontSize: 14,
                  width: 18,
                  flexShrink: 0,
                  color: style.color,
                  textAlign: "center",
                  marginTop: 1,
                }}
              >
                {style.icon}
              </span>
              <span style={{ color: "var(--as-ink-4)", width: 150, flexShrink: 0 }}>
                {fmtTs(e.ts)}
              </span>
              <span style={{ color: "var(--as-ink-3)", width: 60, flexShrink: 0 }}>
                {e.actor}
              </span>
              <span style={{ color: style.color, fontWeight: 700, width: 170, flexShrink: 0 }}>
                {e.event}
              </span>
              <span
                style={{
                  color: "var(--as-ink-2)",
                  flex: 1,
                  minWidth: 0,
                  wordBreak: "break-word",
                }}
              >
                {det}
              </span>
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div style={{ padding: 24, textAlign: "center", color: "var(--as-ink-3)" }}>
            No events match.
          </div>
        )}
      </div>
    </div>
  );
}
