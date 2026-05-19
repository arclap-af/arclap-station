import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Button } from "../../components/Button";
import { Pill } from "../../components/Pill";
import { camera, type CameraProp } from "../../lib/bridge/camera";

export function CameraProperties() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const { data: props } = useQuery({ queryKey: ["camera.properties"], queryFn: camera.properties });

  const filtered = useMemo(
    () => (props ?? []).filter((p: CameraProp) => p.path.toLowerCase().includes(q.toLowerCase())),
    [props, q],
  );

  const grouped = useMemo(() => {
    const g: Record<string, CameraProp[]> = {};
    for (const p of filtered) {
      const segs = p.path.split("/");
      const grp = segs[2] ?? "other";
      (g[grp] ||= []).push(p);
    }
    return g;
  }, [filtered]);

  const save = useMutation({
    mutationFn: ({ path, value }: { path: string; value: string }) => camera.setProperty(path, value),
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["camera.properties"] });
    },
  });

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1100 }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 14 }}>
          <div>
            <h1 className="as-h1">Camera properties</h1>
            <div className="as-h1-sub">
              gphoto2 --list-config · {filtered.length} of {props?.length ?? 0} shown
            </div>
          </div>
          <Link to="/camera" style={{ textDecoration: "none" }}>
            <Button>← Back to viewfinder</Button>
          </Link>
        </div>
        <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--as-line)", background: "var(--as-bg-2)", display: "flex", gap: 8 }}>
            <input
              className="as-input"
              placeholder="Filter path…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ height: 30, fontSize: 12 }}
              aria-label="Filter property path"
            />
          </div>
          <div style={{ maxHeight: 600, overflowY: "auto", fontFamily: "var(--as-mono)", fontSize: 11.5 }}>
            {Object.entries(grouped).map(([grp, rows]) => (
              <div key={grp}>
                <div
                  style={{
                    padding: "5px 14px",
                    background: "var(--as-bg-2)",
                    color: "var(--as-ink-4)",
                    fontSize: 10,
                    textTransform: "uppercase",
                    letterSpacing: 0.08,
                    fontWeight: 700,
                    borderBottom: "1px solid var(--as-line)",
                  }}
                >
                  /main/{grp}
                </div>
                {rows.map((p) => (
                  <div key={p.path} style={{ padding: "6px 14px", borderBottom: "1px solid var(--as-line)" }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ color: "var(--as-ink-3)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {p.path}
                      </span>
                      {editing === p.path ? (
                        <input
                          autoFocus
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") save.mutate({ path: p.path, value: editValue });
                            if (e.key === "Escape") setEditing(null);
                          }}
                          className="as-input mono"
                          style={{ width: 130, height: 22, fontSize: 11, padding: "0 6px" }}
                        />
                      ) : (
                        <span style={{ color: "var(--as-ink)", width: 160, flexShrink: 0, textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.value}
                        </span>
                      )}
                      <Pill tone={!p.readonly ? "ok" : "gray"}>{!p.readonly ? "RW" : "RO"}</Pill>
                      <div style={{ width: 90, flexShrink: 0, display: "flex", gap: 3, justifyContent: "flex-end" }}>
                        {!p.readonly &&
                          (editing === p.path ? (
                            <button
                              type="button"
                              onClick={() => save.mutate({ path: p.path, value: editValue })}
                              style={{
                                padding: "2px 7px",
                                borderRadius: 4,
                                border: "none",
                                background: "var(--as-accent)",
                                color: "#04140e",
                                fontSize: 10,
                                fontFamily: "inherit",
                                cursor: "pointer",
                                fontWeight: 700,
                              }}
                            >
                              OK
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                setEditing(p.path);
                                setEditValue(p.value == null ? "" : String(p.value));
                              }}
                              style={{
                                padding: "2px 7px",
                                borderRadius: 4,
                                border: "1px solid var(--as-accent)",
                                background: "var(--as-accent-soft)",
                                color: "var(--as-accent-2)",
                                fontSize: 10,
                                fontFamily: "inherit",
                                cursor: "pointer",
                                fontWeight: 700,
                              }}
                            >
                              SET
                            </button>
                          ))}
                      </div>
                    </div>
                    {p.choices && editing !== p.path && (
                      <div style={{ fontSize: 10, color: "var(--as-ink-4)", marginTop: 2 }}>↳ {p.choices}</div>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {filtered.length === 0 && (
              <div style={{ padding: 24, textAlign: "center", color: "var(--as-ink-3)" }}>No properties match.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
