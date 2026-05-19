import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Icon, I } from "../components/icons";
import { gallery, type Photo } from "../lib/bridge/gallery";

type Filter = "all" | "uploaded" | "pending" | "starred";

export function Gallery() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Photo | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: photos = [] } = useQuery({
    queryKey: ["gallery", filter, query],
    queryFn: () => gallery.list({ filter, query }),
  });

  const grouped = useMemo(() => {
    const g: Record<string, Photo[]> = {};
    for (const p of photos) {
      const day = p.captured_at.slice(0, 10);
      (g[day] ||= []).push(p);
    }
    return g;
  }, [photos]);

  const removeMut = useMutation({
    mutationFn: gallery.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gallery"] }),
  });
  const starMut = useMutation({
    mutationFn: ({ id, starred }: { id: string; starred: boolean }) => gallery.star(id, starred),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gallery"] }),
  });
  const retryMut = useMutation({
    mutationFn: ({ id, destinationId }: { id: string; destinationId: string }) => gallery.retry(id, destinationId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gallery"] }),
  });

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const clearSel = () => setSelected(new Set());
  const deleteSelected = () => {
    if (!window.confirm(`Delete ${selected.size} photo(s) from SD card? Remote copies are preserved.`)) return;
    Array.from(selected).forEach((id) => removeMut.mutate(id));
    clearSel();
  };

  const totalBytes = photos.reduce((s, p) => s + p.size_bytes, 0);
  const uploaded = photos.filter((p) => p.uploads.some((u) => u.state === "uploaded")).length;
  const pending = photos.length - uploaded;

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1300 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14 }}>
          <div>
            <h1 className="as-h1">Gallery</h1>
            <div className="as-h1-sub">Every photo captured by this station · {photos.length} files · {fmtBytes(totalBytes)}</div>
          </div>
        </div>

        <div className="as-card" style={{ padding: "10px 14px", marginBottom: 14, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <input
            className="as-input"
            placeholder="Search by id or path…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ flex: "1 1 220px", height: 34, fontSize: 12.5 }}
            aria-label="Search gallery"
          />
          <div style={{ display: "flex", border: "1px solid var(--as-line)", borderRadius: 8, overflow: "hidden" }}>
            {([
              ["all", `All · ${photos.length}`],
              ["uploaded", `Uploaded · ${uploaded}`],
              ["pending", `Pending · ${pending}`],
              ["starred", "Starred"],
            ] as const).map(([id, n]) => (
              <button
                key={id}
                type="button"
                onClick={() => setFilter(id)}
                style={{
                  padding: "7px 12px",
                  border: "none",
                  background: filter === id ? "var(--as-accent-soft)" : "transparent",
                  color: filter === id ? "var(--as-accent-2)" : "var(--as-ink-2)",
                  fontSize: 11.5,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  borderRight: id === "starred" ? "none" : "1px solid var(--as-line)",
                }}
              >
                {n}
              </button>
            ))}
          </div>
          {selected.size > 0 && (
            <>
              <div style={{ fontSize: 12, color: "var(--as-ink-2)", fontWeight: 600 }}>{selected.size} selected</div>
              <Button style={{ padding: "6px 10px", fontSize: 11.5, color: "var(--as-bad)" }} onClick={deleteSelected}>
                Delete
              </Button>
              <Button style={{ padding: "6px 10px", fontSize: 11.5 }} onClick={clearSel}>
                Clear
              </Button>
            </>
          )}
        </div>

        {Object.entries(grouped).map(([day, list]) => (
          <div key={day} style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10, padding: "0 4px" }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--as-ink-2)" }}>{day}</div>
              <div style={{ fontSize: 11.5, color: "var(--as-ink-3)" }}>
                {list.length} photos · {fmtBytes(list.reduce((s, p) => s + p.size_bytes, 0))}
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 10 }}>
              {list.map((p) => {
                const up = p.uploads.find((u) => u.state === "uploaded");
                return (
                  <div
                    key={p.id}
                    style={{
                      position: "relative",
                      borderRadius: 8,
                      overflow: "hidden",
                      cursor: "pointer",
                      aspectRatio: "3/2",
                      border: selected.has(p.id) ? "2px solid var(--as-accent)" : "1px solid var(--as-line)",
                      background: "#0f1a26",
                    }}
                    onClick={() => setOpen(p)}
                  >
                    <img
                      loading="lazy"
                      src={p.thumb_url}
                      alt={p.filename}
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    />
                    <div style={{ position: "absolute", top: 6, left: 6, right: 6, display: "flex", justifyContent: "space-between" }}>
                      <input
                        type="checkbox"
                        checked={selected.has(p.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggle(p.id)}
                        style={{ width: 16, height: 16, accentColor: "var(--as-accent)" }}
                        aria-label={`Select ${p.filename}`}
                      />
                      <Pill tone={up ? "ok" : "warn"} className="!text-[9.5px] !px-1.5">
                        {up ? "Synced" : "Local"}
                      </Pill>
                    </div>
                    <div
                      style={{
                        position: "absolute",
                        bottom: 6,
                        left: 8,
                        right: 8,
                        color: "#fff",
                        fontSize: 10,
                        fontFamily: "var(--as-mono)",
                        display: "flex",
                        justifyContent: "space-between",
                        textShadow: "0 0 4px #000",
                      }}
                    >
                      <span>{p.filename}</span>
                      <span style={{ opacity: 0.8 }}>{p.captured_at.slice(11, 16)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {photos.length === 0 && (
          <div className="as-card" style={{ padding: 48, textAlign: "center" }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>No photos yet</div>
            <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>Trigger a capture from the Camera page to start.</div>
          </div>
        )}

        {open && (
          <div
            role="dialog"
            aria-modal="true"
            onClick={() => setOpen(null)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.8)",
              zIndex: 50,
              display: "grid",
              placeItems: "center",
              padding: 24,
              backdropFilter: "blur(6px)",
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              className="as-card"
              style={{
                maxWidth: 1100,
                width: "100%",
                maxHeight: "92vh",
                display: "grid",
                gridTemplateColumns: "1fr 320px",
                padding: 0,
                overflow: "hidden",
              }}
            >
              <div style={{ background: "#000", position: "relative", display: "grid", placeItems: "center", overflow: "hidden", minHeight: 400 }}>
                <img src={open.original_url} alt={open.filename} style={{ width: "100%", height: "100%", objectFit: "contain" }} />
                <button
                  type="button"
                  onClick={() => setOpen(null)}
                  aria-label="Close"
                  style={{
                    position: "absolute",
                    top: 12,
                    right: 12,
                    width: 32,
                    height: 32,
                    borderRadius: 16,
                    background: "rgba(0,0,0,0.6)",
                    color: "#fff",
                    border: "none",
                    cursor: "pointer",
                    fontSize: 18,
                  }}
                >
                  ×
                </button>
              </div>
              <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 14, overflowY: "auto", borderLeft: "1px solid var(--as-line)" }}>
                <div>
                  <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 700, marginBottom: 4 }}>File</div>
                  <div className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{open.filename}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--as-ink-4)", marginTop: 3, wordBreak: "break-all" }}>{open.path}</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 700, marginBottom: 6 }}>Exposure</div>
                  <Row label="ISO" val={open.iso} />
                  <Row label="Shutter" val={`${open.shutter}s`} />
                  <Row label="Aperture" val={open.aperture} />
                  <Row label="Size" val={`${open.width}×${open.height} · ${fmtBytes(open.size_bytes)}`} />
                </div>
                <div>
                  <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 700, marginBottom: 6 }}>Uploads</div>
                  {open.uploads.length === 0 && <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>No destinations configured.</div>}
                  {open.uploads.map((u) => (
                    <div key={u.destination_id} className="as-stat-row">
                      <span className="as-stat-label">{u.destination_name}</span>
                      <span className="as-stat-val">
                        {u.state === "uploaded" ? (
                          <Pill tone="ok">✓ {u.uploaded_at?.slice(11, 19) ?? "synced"}</Pill>
                        ) : u.state === "failed" ? (
                          <Pill tone="bad">Failed</Pill>
                        ) : u.state === "in_progress" ? (
                          <Pill tone="warn">Uploading</Pill>
                        ) : (
                          <Pill tone="warn">Pending</Pill>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: "auto" }}>
                  <a className="as-btn as-btn-primary" href={open.original_url} download>
                    <Icon d={I.upload} size={14} /> Download original
                  </a>
                  <Button
                    onClick={() => {
                      starMut.mutate({ id: open.id, starred: !open.starred });
                      setOpen({ ...open, starred: !open.starred });
                    }}
                  >
                    {open.starred ? "★ Unstar" : "☆ Star"}
                  </Button>
                  {open.uploads.some((u) => u.state !== "uploaded") && (
                    <Button
                      onClick={() => {
                        const failed = open.uploads.find((u) => u.state !== "uploaded");
                        if (failed) retryMut.mutate({ id: open.id, destinationId: failed.destination_id });
                      }}
                    >
                      ↑ Retry upload
                    </Button>
                  )}
                  <Button
                    style={{ color: "var(--as-bad)" }}
                    onClick={() => {
                      removeMut.mutate(open.id);
                      setOpen(null);
                    }}
                  >
                    Delete from SD card
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, val }: { label: string; val: string }) {
  return (
    <div className="as-stat-row">
      <span className="as-stat-label">{label}</span>
      <span className="as-stat-val mono">{val}</span>
    </div>
  );
}

function fmtBytes(b: number): string {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  if (b >= 1e3) return `${(b / 1e3).toFixed(1)} KB`;
  return `${b} B`;
}
