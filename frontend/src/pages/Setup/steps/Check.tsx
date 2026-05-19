import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Icon, I } from "../../../components/icons";
import { acceptance } from "../../../lib/bridge/acceptance";
import type { AcceptanceResult, AcceptanceRun } from "../../../lib/bridge/acceptance";

interface Props {
  onResult: (passed: boolean) => void;
}

export function Check({ onResult }: Props) {
  const [run, setRun] = useState<AcceptanceRun | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const start = useMutation({
    mutationFn: acceptance.start,
    onSuccess: (r) => {
      setRun(r);
      if (pollRef.current) clearInterval(pollRef.current);
      if (r.status === "running") {
        pollRef.current = setInterval(async () => {
          try {
            const updated = await acceptance.status(r.run_id);
            setRun(updated);
            if (updated.status !== "running") {
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
            }
          } catch {
            // ignore transient
          }
        }, 800);
      }
    },
  });

  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    [],
  );

  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  useEffect(() => {
    onResultRef.current(run?.status === "pass");
  }, [run?.status]);

  const grouped: Record<string, AcceptanceResult[]> = {};
  for (const r of run?.results ?? []) {
    (grouped[r.group] ||= []).push(r);
  }
  const done = run?.results.length ?? 0;
  const total = Math.max(done, 40);
  const passed = run?.results.filter((r) => r.ok).length ?? 0;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>
            {done} / {total} run · {passed} pass
          </div>
          <div style={{ fontSize: 11.5, color: "var(--as-ink-3)", marginTop: 2 }}>
            {run?.status === "running"
              ? "Running…"
              : run?.status === "pass"
                ? "Ready to ship"
                : run?.status === "fail"
                  ? "Some checks failed"
                  : "Press run to start"}
          </div>
        </div>
        <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending || run?.status === "running"}>
          <Icon d={I.zap} size={14} />
          {run?.status === "running" ? "Running" : done === 0 ? "Run all" : "Re-run"}
        </Button>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--as-surface-2)", overflow: "hidden", marginBottom: 18 }}>
        <div style={{ height: "100%", width: `${(done / total) * 100}%`, background: "var(--as-accent)", transition: "width 200ms" }} />
      </div>
      {Object.entries(grouped).map(([group, rows]) => (
        <div key={group} style={{ marginBottom: 12 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--as-ink-3)",
              textTransform: "uppercase",
              letterSpacing: 0.06,
              marginBottom: 6,
            }}
          >
            {group}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {rows.map((r) => (
              <div
                key={r.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "5px 10px",
                  borderRadius: 14,
                  background: r.ok ? "var(--as-accent-soft)" : "rgba(239,68,68,0.15)",
                  color: r.ok ? "var(--as-accent-2)" : "var(--as-bad)",
                  fontSize: 11.5,
                  fontWeight: 600,
                }}
                title={r.detail ?? undefined}
              >
                {r.ok ? <Icon d={I.check} size={10} stroke={3} /> : <Icon d={I.close} size={10} stroke={3} />}
                {r.name}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
