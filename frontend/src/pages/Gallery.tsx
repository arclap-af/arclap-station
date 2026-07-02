import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Pill } from "../components/Pill";
import { Icon, I } from "../components/icons";
import { gallery, type Photo } from "../lib/bridge/gallery";

type Filter = "all" | "uploaded" | "pending" | "starred";

/**
 * Thumbnail that degrades gracefully when the source file is gone.
 *
 * With a schedule's "keep local copy" turned off, the SD-card file is
 * deleted once every destination has the photo — so its thumbnail 410s.
 * Rather than show a broken-image icon, render a tidy placeholder:
 * "Uploaded · cleared" when the photo did upload (`cleared`), or
 * "Preview unavailable" otherwise.
 */
function Thumb({ src, alt, cleared }: { src: string; alt: string; cleared: boolean }) {
  const [errored, setErrored] = useState(false);
  if (errored) {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "grid",
          placeItems: "center",
          background: "#0f1a26",
          color: "var(--as-ink-3)",
          textAlign: "center",
          padding: 8,
        }}
      >
        <div>
          <Icon d={cleared ? I.upload : I.gallery} size={20} />
          <div style={{ fontSize: 9.5, marginTop: 4, lineHeight: 1.3 }}>
            {cleared ? "Uploaded · cleared from device" : "Preview unavailable"}
          </div>
        </div>
      </div>
    );
  }
  return (
    <img
      loading="lazy"
      src={src}
      alt={alt}
      onError={() => setErrored(true)}
      style={{ width: "100%", height: "100%", objectFit: "cover" }}
    />
  );
}

export function Gallery() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Photo | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const PAGE = 200;
  const {
    data,
    refetch,
    isFetching,
    dataUpdatedAt,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["gallery", filter, query],
    // Paginated so a station with thousands of photos isn't silently
    // capped at the first 100 (counts, Select-all and Delete-all used to
    // operate only on that first page).
    queryFn: ({ pageParam }) =>
      gallery.listPage({ filter, query, limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    // Auto-refresh every 10 s so newly-captured photos appear without a
    // manual click. Infinite queries refetch all loaded pages.
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  });
  const photos: Photo[] = useMemo(
    () => (data?.pages ?? []).flatMap((p) => p.items),
    [data],
  );
  const total = data?.pages?.[0]?.total ?? photos.length;

  // Tick once per second so the "Updated Xs ago" label is live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const updatedAgoSec = dataUpdatedAt
    ? Math.max(0, Math.floor((now - dataUpdatedAt) / 1000))
    : null;

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
  const bulkDeleteMut = useMutation({
    mutationFn: gallery.bulkDelete,
    onSuccess: () => {
      clearSel();
      qc.invalidateQueries({ queryKey: ["gallery"] });
    },
    onError: (e) =>
      window.alert("Delete failed: " + (e instanceof Error ? e.message : String(e))),
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

  // True when every currently-visible photo is in the selection. Used
  // to flip the Select-all button into a Deselect-all (per the
  // standard tri-state checkbox UX).
  const allVisibleSelected =
    photos.length > 0 && photos.every((p) => selected.has(p.id));

  const selectAllVisible = () => {
    if (allVisibleSelected) {
      // Re-clicking when everything is already selected toggles off.
      setSelected(new Set());
    } else {
      setSelected(new Set(photos.map((p) => p.id)));
    }
  };

  const deleteSelected = () => {
    if (selected.size === 0) return;
    if (!window.confirm(`Delete ${selected.size} photo(s) from the SD card? Remote copies on configured destinations are preserved.`)) return;
    // One request with error handling — not N fire-and-forget mutations.
    bulkDeleteMut.mutate({ ids: Array.from(selected) });
  };

  const deleteAll = () => {
    // Two-step confirm — first the count, then the literal word so a
    // mis-click can't wipe the gallery. Deletes the WHOLE matching set
    // server-side (not just the loaded page).
    if (total === 0) return;
    if (!window.confirm(`Delete ALL ${total} photo(s) matching this view?\n\nRemote copies are preserved. This cannot be undone locally.`)) return;
    const typed = window.prompt(`Type DELETE to confirm removing ${total} photos.`);
    if (typed !== "DELETE") return;
    bulkDeleteMut.mutate({ all: true, filter, query });
  };

  const totalBytes = photos.reduce((s, p) => s + p.size_bytes, 0);
  const uploaded = photos.filter((p) => p.uploads.some((u) => u.state === "uploaded")).length;
  const pending = photos.length - uploaded;

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1300 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14, gap: 12 }}>
          <div>
            <h1 className="as-h1">Gallery</h1>
            <div className="as-h1-sub">Every photo captured by this station · {total} files{photos.length < total ? ` (${photos.length} loaded)` : ""} · {fmtBytes(totalBytes)}</div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span
              style={{
                fontSize: 11,
                color: "var(--as-ink-3)",
                fontFamily: "var(--as-mono)",
                minWidth: 110,
                textAlign: "right",
              }}
              title={
                dataUpdatedAt
                  ? `Last refreshed at ${new Date(dataUpdatedAt).toLocaleTimeString()}`
                  : "Not refreshed yet"
              }
            >
              {isFetching
                ? "refreshing…"
                : updatedAgoSec === null
                  ? "—"
                  : updatedAgoSec < 2
                    ? "just now"
                    : `updated ${updatedAgoSec}s ago`}
            </span>
            <Button
              style={{ padding: "6px 12px", fontSize: 12 }}
              onClick={() => refetch()}
              disabled={isFetching}
              aria-label="Refresh gallery"
              title="Force-refresh now (the gallery also auto-refreshes every 10 s)"
            >
              <Icon d={I.refresh} size={13} /> Refresh
            </Button>
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
              ["all", `All · ${total}`],
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
          {/* Bulk-selection toolbar — always visible (not hidden behind
              a selection) so the operator knows the option exists. */}
          <Button
            style={{ padding: "6px 10px", fontSize: 11.5 }}
            onClick={selectAllVisible}
            disabled={photos.length === 0}
            title={allVisibleSelected ? "Deselect every visible photo" : "Select every visible photo"}
          >
            {allVisibleSelected ? `Deselect all (${photos.length})` : `Select all (${photos.length})`}
          </Button>

          {selected.size > 0 && (
            <>
              <div style={{ fontSize: 12, color: "var(--as-ink-2)", fontWeight: 600 }}>
                {selected.size} selected
              </div>
              <Button
                style={{ padding: "6px 10px", fontSize: 11.5, color: "var(--as-bad)", fontWeight: 600 }}
                onClick={deleteSelected}
              >
                Delete {selected.size}
              </Button>
              <Button style={{ padding: "6px 10px", fontSize: 11.5 }} onClick={clearSel}>
                Clear
              </Button>
            </>
          )}

          {/* "Delete all" stays separate from the selection-based bulk
              delete so a click here is unambiguously "everything",
              not the dynamic N from the current selection. Two-step
              confirm (count + DELETE keyword) inside deleteAll(). */}
          {photos.length > 0 && selected.size === 0 && (
            <Button
              style={{ padding: "6px 10px", fontSize: 11.5, color: "var(--as-bad)", marginLeft: "auto" }}
              onClick={deleteAll}
              title="Remove every photo from the SD card. Remote copies are preserved."
            >
              Delete all ({photos.length})
            </Button>
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
                    <Thumb src={p.thumb_url} alt={p.filename} cleared={!!up} />
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

        {hasNextPage && (
          <div style={{ textAlign: "center", marginTop: 4, marginBottom: 8 }}>
            <Button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
              {isFetchingNextPage ? "Loading…" : `Load more · ${photos.length} of ${total}`}
            </Button>
          </div>
        )}

        {photos.length === 0 && (
          <div className="as-card" style={{ padding: 0 }}>
            <EmptyState
              icon={I.gallery}
              title={query || filter !== "all" ? "No photos match" : "No photos yet"}
              message={
                query || filter !== "all"
                  ? "Try clearing the search or switching the filter back to All."
                  : "Captures land here automatically. Take one from the Camera page or wait for the next scheduled capture."
              }
              action={
                !query && filter === "all" ? (
                  <Link to="/camera" style={{ textDecoration: "none" }}>
                    <Button variant="primary" style={{ padding: "8px 16px", fontSize: 13 }}>
                      <Icon d={I.camera} size={14} /> Open camera
                    </Button>
                  </Link>
                ) : (
                  <Button
                    style={{ padding: "8px 16px", fontSize: 13 }}
                    onClick={() => {
                      setQuery("");
                      setFilter("all");
                    }}
                  >
                    Clear filters
                  </Button>
                )
              }
            />
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
              className="as-card as-lightbox"
              style={{
                maxWidth: 1100,
                width: "100%",
                maxHeight: "92vh",
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
