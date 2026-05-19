import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { settings, type LogEntry } from "../../../lib/bridge/settings";
import { useWebSocket } from "../../../lib/ws";

const UNITS = ["all", "arclap-station", "arclap-uploader", "kernel"] as const;
const LEVELS = ["all", "info", "warn", "error"] as const;

export function Logs() {
  const [unit, setUnit] = useState<(typeof UNITS)[number]>("all");
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("all");
  const [query, setQuery] = useState("");
  const [paused, setPaused] = useState(false);
  const [live, setLive] = useState<LogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const { data: history = [] } = useQuery({
    queryKey: ["settings.logs", unit, level, query],
    queryFn: () => settings.logs(unit, level, query || undefined),
  });

  const onMessage = useCallback((ev: MessageEvent) => {
    try {
      const payload = JSON.parse(ev.data) as LogEntry;
      setLive((prev) => [...prev.slice(-499), payload]);
    } catch {
      // ignore
    }
  }, []);
  const wsParams = new URLSearchParams();
  if (unit !== "all") wsParams.set("unit", unit);
  if (level !== "all") wsParams.set("level", level);
  useWebSocket(`/api/v1/settings/logs/ws?${wsParams.toString()}`, onMessage);

  useEffect(() => {
    if (paused) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [history, live, paused]);

  const all = [...history, ...live];
  const filtered = all.filter((l) => {
    if (level !== "all" && l.level !== level) return false;
    if (unit !== "all" && l.unit !== unit) return false;
    if (query && !l.message.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--as-line)", background: "var(--as-bg-2)", display: "flex", gap: 8, alignItems: "center" }}>
        <input
          className="as-input"
          placeholder="Filter logs…"
          style={{ flex: 1, height: 30, fontSize: 12 }}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter log text"
        />
        <select className="as-select" style={{ width: 180, height: 30, fontSize: 12 }} value={unit} onChange={(e) => setUnit(e.target.value as (typeof UNITS)[number])} aria-label="Unit">
          {UNITS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <select className="as-select" style={{ width: 120, height: 30, fontSize: 12 }} value={level} onChange={(e) => setLevel(e.target.value as (typeof LEVELS)[number])} aria-label="Level">
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        <Button style={{ padding: "6px 10px", fontSize: 12 }} onClick={() => setPaused((v) => !v)}>
          {paused ? "Resume" : "Pause"}
        </Button>
      </div>
      <div ref={scrollRef} style={{ fontFamily: "var(--as-mono)", fontSize: 11.5, padding: 10, lineHeight: 1.7, maxHeight: 600, overflowY: "auto" }}>
        {filtered.map((l, i) => (
          <div key={i} style={{ display: "flex", gap: 10, padding: "4px 6px" }}>
            <span style={{ color: "var(--as-ink-4)", width: 168, flexShrink: 0 }}>{l.ts}</span>
            <span style={{ color: "var(--as-ink-3)", width: 130, flexShrink: 0 }}>{l.unit}</span>
            <span
              style={{
                color: l.level === "error" ? "var(--as-bad)" : l.level === "warn" ? "var(--as-warn)" : "var(--as-accent-2)",
                width: 46,
                flexShrink: 0,
                fontWeight: 700,
              }}
            >
              {l.level}
            </span>
            <span style={{ color: "var(--as-ink-2)", flex: 1, minWidth: 0, wordBreak: "break-word" }}>{l.message}</span>
          </div>
        ))}
        {filtered.length === 0 && <div style={{ padding: 24, textAlign: "center", color: "var(--as-ink-3)" }}>No log lines match.</div>}
      </div>
    </div>
  );
}
