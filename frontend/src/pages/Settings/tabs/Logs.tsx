import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { settings, type LogEntry } from "../../../lib/bridge/settings";
import { useWebSocket } from "../../../lib/ws";

// The actual systemd units that exist on a deployed station. Anything
// not here would have nothing in journalctl — the previous hardcoded
// list included "arclap-uploader" (a service that doesn't exist) and
// "kernel" (deliberately not surfaced; the cockpit is an operator
// view, not a debugger).
const UNITS = [
  { value: "all", label: "All units" },
  { value: "arclap-station", label: "arclap-station" },
  { value: "caddy", label: "caddy" },
] as const;

const LEVELS = [
  { value: "all", label: "All levels" },
  { value: "info", label: "info" },
  { value: "warn", label: "warn" },
  { value: "error", label: "error" },
] as const;

type UnitValue = (typeof UNITS)[number]["value"];
type LevelValue = (typeof LEVELS)[number]["value"];

export function Logs() {
  const [unit, setUnit] = useState<UnitValue>("all");
  const [level, setLevel] = useState<LevelValue>("all");
  const [query, setQuery] = useState("");
  const [paused, setPaused] = useState(false);
  const [live, setLive] = useState<LogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // History pull: backend filters server-side via unit/level/q so we
  // don't pay for transferring 100s of irrelevant lines just to drop
  // them client-side. Backend returns newest-first already.
  const { data: history = [], refetch } = useQuery({
    queryKey: ["settings.logs", unit, level, query],
    queryFn: () => settings.logs(unit, level, query || undefined),
    // Re-pull whenever the dropdowns / search change.
    staleTime: 5_000,
  });

  // Live stream — reset when the unit filter changes so we don't see
  // tail entries from the previously-watched unit mixed in.
  useEffect(() => {
    setLive([]);
  }, [unit]);

  const onMessage = useCallback(
    (ev: MessageEvent) => {
      if (paused) return;
      try {
        const payload = JSON.parse(ev.data) as LogEntry;
        // Hard cap the buffer so a chatty service doesn't grow live[]
        // unbounded — the user always has access to the full history
        // via the recent-history pull anyway.
        setLive((prev) => [payload, ...prev].slice(0, 500));
      } catch {
        // ignore non-JSON frames
      }
    },
    [paused],
  );
  // WS unit param matches the backend `_resolve_unit()` aliases.
  const wsParams = new URLSearchParams();
  if (unit !== "all") wsParams.set("unit", unit);
  useWebSocket(`/api/settings/logs-ws?${wsParams.toString()}`, onMessage);

  // Merge live + history, then sort newest-first on the ts field.
  // Both sources hand us {ts: ISO 8601} so a lexicographic sort works
  // and is monotonic — no Date() parsing needed.
  const merged = useMemo(() => {
    const all = [...live, ...history];
    return all
      .filter((l) => {
        // Level/unit filtering on the server can lag behind a freshly
        // changed dropdown by a tick — apply the same filter on the
        // client so transient mismatches don't show stale rows.
        if (level !== "all" && l.level !== level) return false;
        if (unit !== "all" && !l.unit.startsWith(unit)) return false;
        if (query && !l.message.toLowerCase().includes(query.toLowerCase())) return false;
        return true;
      })
      .sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
  }, [live, history, level, unit, query]);

  // Scroll to TOP when new entries arrive (the newest is at index 0).
  // Paused mode pins the scroll so the operator can read without the
  // page jumping under them.
  useEffect(() => {
    if (paused) return;
    scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [merged.length, paused]);

  const fmtTs = (iso: string): string => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      // YYYY-MM-DD HH:MM:SS in local time — short + parseable.
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    } catch {
      return iso;
    }
  };

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
        }}
      >
        <input
          className="as-input"
          placeholder="Search messages…"
          style={{ flex: 1, height: 30, fontSize: 12 }}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter log text"
        />
        <select
          className="as-select"
          style={{ width: 160, height: 30, fontSize: 12 }}
          value={unit}
          onChange={(e) => setUnit(e.target.value as UnitValue)}
          aria-label="Unit"
        >
          {UNITS.map((u) => (
            <option key={u.value} value={u.value}>
              {u.label}
            </option>
          ))}
        </select>
        <select
          className="as-select"
          style={{ width: 120, height: 30, fontSize: 12 }}
          value={level}
          onChange={(e) => setLevel(e.target.value as LevelValue)}
          aria-label="Level"
        >
          {LEVELS.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
        <Button
          style={{ padding: "6px 10px", fontSize: 12 }}
          onClick={() => setPaused((v) => !v)}
        >
          {paused ? "Resume" : "Pause"}
        </Button>
        <Button
          style={{ padding: "6px 10px", fontSize: 12 }}
          onClick={() => {
            setLive([]);
            refetch();
          }}
        >
          Clear
        </Button>
      </div>
      <div
        ref={scrollRef}
        style={{
          fontFamily: "var(--as-mono)",
          fontSize: 11.5,
          padding: 10,
          lineHeight: 1.7,
          maxHeight: 600,
          overflowY: "auto",
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: "var(--as-ink-3)",
            padding: "0 6px 8px",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>
            {merged.length} {merged.length === 1 ? "line" : "lines"} · newest first
            {paused ? " · paused" : ""}
          </span>
          <span>
            live: {live.length} · history: {history.length}
          </span>
        </div>
        {merged.map((l, i) => (
          <div key={`${l.ts}-${i}`} style={{ display: "flex", gap: 10, padding: "4px 6px" }}>
            <span style={{ color: "var(--as-ink-4)", width: 168, flexShrink: 0 }}>
              {fmtTs(l.ts)}
            </span>
            <span style={{ color: "var(--as-ink-3)", width: 150, flexShrink: 0 }}>
              {l.unit.replace(/\.service$/, "")}
            </span>
            <span
              style={{
                color:
                  l.level === "error"
                    ? "var(--as-bad)"
                    : l.level === "warn"
                      ? "var(--as-warn)"
                      : "var(--as-accent-2)",
                width: 46,
                flexShrink: 0,
                fontWeight: 700,
              }}
            >
              {l.level}
            </span>
            <span
              style={{
                color: "var(--as-ink-2)",
                flex: 1,
                minWidth: 0,
                wordBreak: "break-word",
              }}
            >
              {l.message}
            </span>
          </div>
        ))}
        {merged.length === 0 && (
          <div style={{ padding: 24, textAlign: "center", color: "var(--as-ink-3)" }}>
            No log lines match.
          </div>
        )}
      </div>
    </div>
  );
}
