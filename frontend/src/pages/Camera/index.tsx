import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/Button";
import { Pill } from "../../components/Pill";
import { useToast } from "../../components/ToastQueue";
import { apiFetch } from "../../lib/api";
import { camera, type CameraSettings } from "../../lib/bridge/camera";

// v0.9 Camera page — still-image only, no live MJPEG viewfinder.
// Operator workflow:
//   1. Open Camera page
//   2. (Optionally) tap chips to pick ISO / shutter / aperture / mode
//   3. Press Capture
//   4. The just-taken photo replaces the placeholder
// Reconnect / USB reset / MLU / sync-clock / Properties live in the header.

export function CameraPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [lastPhoto, setLastPhoto] = useState<{
    id: number;
    filename: string;
    size_bytes: number;
    captured_at?: string;
    width?: number | null;
    height?: number | null;
    exif?: Record<string, unknown> | null;
  } | null>(null);

  const { data: info } = useQuery({
    queryKey: ["camera.info"],
    queryFn: camera.info,
    refetchInterval: 30_000,
  });
  const { data: settings } = useQuery({
    queryKey: ["camera.settings"],
    queryFn: camera.settings,
  });
  const patch = useMutation({
    mutationFn: (p: Partial<CameraSettings>) => camera.updateSettings(p),
    onSuccess: (s) => qc.setQueryData(["camera.settings"], s),
    onError: (e) => toast.show(e instanceof Error ? e.message : "Setting change failed", "bad"),
  });

  const capture = useMutation({
    mutationFn: () => apiFetch<{
      id: number; filename: string; size_bytes: number; captured_at?: string;
      width?: number | null; height?: number | null; exif?: Record<string, unknown> | null;
    }>("/camera/capture", { method: "POST" }),
    onSuccess: (r) => {
      setLastPhoto(r);
      toast.show(`Captured ${r.filename}`, "ok");
      // Refresh info so the latest health beacon state shows.
      qc.invalidateQueries({ queryKey: ["camera.info"] });
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : "Capture failed", "bad"),
  });
  const reconnect = useMutation({
    mutationFn: camera.reconnect,
    onSuccess: (r) => {
      toast.show(r?.ok ? "Camera reconnected" : "Reconnected (no camera detected)", r?.ok ? "ok" : "warn");
      qc.invalidateQueries({ queryKey: ["camera.info"] });
    },
    onError: (e) => toast.show(e instanceof Error ? e.message : "Reconnect failed", "bad"),
  });
  const syncClock = useMutation({
    mutationFn: camera.syncClock,
    onSuccess: () => toast.show("Clock synced", "ok"),
    onError: (e) => toast.show(e instanceof Error ? e.message : "Sync failed", "bad"),
  });
  const usbReset = useMutation({
    mutationFn: camera.usbReset,
    onSuccess: () => toast.show("USB bus reset + re-scanned", "ok"),
    onError: (e) => toast.show(e instanceof Error ? e.message : "USB reset failed", "bad"),
  });
  const mlu = useMutation({
    mutationFn: () =>
      apiFetch("/camera/settings", {
        method: "PUT",
        body: { path: "/main/capturesettings/mirrorlockup", value: "On" },
      }),
    onSuccess: () => toast.show("Mirror lock-up engaged", "ok"),
    onError: () => toast.show("MLU not supported on this body", "warn"),
  });

  const exp = settings;
  const MODES = info?.choices.mode ?? [];
  const ISOS = info?.choices.iso ?? [];
  const SHUTTERS = info?.choices.shutter ?? [];
  const APERTURES = info?.choices.aperture ?? [];

  const detected = Boolean(info?.detected);
  const lastError = info?.health?.last_error;

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1300 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14, gap: 14, flexWrap: "wrap" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <h1 className="as-h1" style={{ margin: 0 }}>Camera</h1>
              {(() => {
                if (!info) return <Pill tone="gray">probing…</Pill>;
                if (!detected) return <Pill tone="bad">no camera</Pill>;
                if (lastError && !info.health.ok) {
                  return <Pill tone="warn">PTP error · {String(lastError).slice(0, 40)}</Pill>;
                }
                return <Pill tone="ok">connected</Pill>;
              })()}
            </div>
            <div className="as-h1-sub" style={{ marginBottom: 0 }}>
              {info?.model ? `${info.model} · ${info.lens ?? "no lens info"}` : "Tethered DSLR via gphoto2"}
            </div>
          </div>
          <div className="as-row-actions" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Button onClick={() => reconnect.mutate()} disabled={reconnect.isPending}>
              {reconnect.isPending ? "Reconnecting…" : "Reconnect"}
            </Button>
            <Button onClick={() => usbReset.mutate()} disabled={usbReset.isPending}>
              {usbReset.isPending ? "Resetting…" : "USB reset"}
            </Button>
            <Button onClick={() => mlu.mutate()} disabled={mlu.isPending || !detected}>MLU</Button>
            <Button onClick={() => syncClock.mutate()} disabled={syncClock.isPending || !detected}>Sync clock</Button>
            <Link to="/camera/properties" style={{ textDecoration: "none" }}>
              <Button>Properties</Button>
            </Link>
          </div>
        </div>

        {/* Big capture button + last photo */}
        <div className="as-grid-2" style={{ alignItems: "start", gap: 14, gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.4fr)" }}>
          {/* Left: chips + capture */}
          <div className="as-card">
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Quick settings</div>

            <ChipRow label="Mode" choices={MODES.length ? MODES : ["M", "Av", "Tv", "P"]}
              value={info?.values.mode ?? exp?.mode ?? "M"}
              onPick={(v) => patch.mutate({ mode: v })} />
            <ChipRow label="ISO" choices={ISOS.length ? ISOS.slice(0, 12) : ["100", "200", "400", "800", "1600", "3200"]}
              value={info?.values.iso ?? exp?.iso ?? "—"}
              onPick={(v) => patch.mutate({ iso: v })} />
            <ChipRow label="Shutter" choices={SHUTTERS.length ? SHUTTERS.slice(0, 12) : ["1/4000", "1/1000", "1/250", "1/60", "1/15", "1"]}
              value={info?.values.shutter ?? exp?.shutter ?? "—"}
              onPick={(v) => patch.mutate({ shutter: v })} />
            <ChipRow label="Aperture" choices={APERTURES.length ? APERTURES.slice(0, 12) : ["1.8", "2.8", "4", "5.6", "8", "11"]}
              value={info?.values.aperture ?? exp?.aperture ?? "—"}
              onPick={(v) => patch.mutate({ aperture: v })} />

            <div style={{ marginTop: 18 }}>
              <Button
                variant="primary"
                onClick={() => capture.mutate()}
                disabled={capture.isPending || !detected}
                style={{
                  width: "100%",
                  padding: "16px 0",
                  fontSize: 16,
                  fontWeight: 700,
                  letterSpacing: 0.04,
                }}
              >
                {capture.isPending ? "Capturing…" : detected ? "📸 Capture" : "Camera not connected"}
              </Button>
              {!detected && (
                <div style={{ marginTop: 10, fontSize: 12, color: "var(--as-ink-3)", lineHeight: 1.5 }}>
                  Check: cable plugged in both ends, camera powered on (not auto-off),
                  USB mode is PTP (not card-reader), then press <strong>Reconnect</strong> or <strong>USB reset</strong>.
                </div>
              )}
              {lastError && (
                <div style={{ marginTop: 10, fontSize: 11, fontFamily: "var(--as-mono)", color: "var(--as-bad)", wordBreak: "break-all" }}>
                  Last error: {String(lastError)}
                </div>
              )}
            </div>
          </div>

          {/* Right: last-captured photo */}
          <div className="as-card" style={{ padding: 0, overflow: "hidden", minHeight: 380 }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--as-line)", fontSize: 13, fontWeight: 700 }}>
              {lastPhoto ? `Last capture · ${lastPhoto.filename}` : "Last capture"}
            </div>
            {lastPhoto ? (
              <>
                <div style={{ background: "#06090d", display: "flex", justifyContent: "center", alignItems: "center", minHeight: 320 }}>
                  <img
                    src={`/api/gallery/${lastPhoto.id}/full`}
                    alt={lastPhoto.filename}
                    style={{
                      maxWidth: "100%",
                      maxHeight: 560,
                      objectFit: "contain",
                      display: "block",
                    }}
                  />
                </div>
                <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--as-ink-3)" }}>
                  {lastPhoto.width && lastPhoto.height && (
                    <span style={{ marginRight: 14 }}>{lastPhoto.width}×{lastPhoto.height}px</span>
                  )}
                  <span style={{ marginRight: 14 }}>{(lastPhoto.size_bytes / (1024 * 1024)).toFixed(1)} MB</span>
                  {lastPhoto.exif && (
                    <>
                      {lastPhoto.exif.iso !== undefined && <span style={{ marginRight: 10 }}>ISO {String(lastPhoto.exif.iso)}</span>}
                      {lastPhoto.exif.shutter && <span style={{ marginRight: 10 }}>{String(lastPhoto.exif.shutter)}s</span>}
                      {lastPhoto.exif.aperture && <span style={{ marginRight: 10 }}>{String(lastPhoto.exif.aperture)}</span>}
                      {lastPhoto.exif.lens && <span style={{ marginRight: 10 }}>{String(lastPhoto.exif.lens)}</span>}
                    </>
                  )}
                  {lastPhoto.captured_at && (
                    <span style={{ color: "var(--as-ink-4)", marginLeft: "auto" }}>
                      {new Date(lastPhoto.captured_at).toLocaleString()}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <div style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "60px 20px",
                color: "var(--as-ink-3)",
                fontSize: 14,
              }}>
                <div style={{ fontSize: 48, marginBottom: 14, opacity: 0.4 }}>📷</div>
                <div>No capture yet.</div>
                <div style={{ marginTop: 6, fontSize: 12 }}>
                  Press <strong>Capture</strong> to take a still — it will show up here.
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ChipRow({
  label, choices, value, onPick,
}: { label: string; choices: string[]; value: string; onPick: (v: string) => void }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginBottom: 5, textTransform: "uppercase", letterSpacing: 0.06 }}>
        {label} <span style={{ color: "var(--as-ink-2)", fontFamily: "var(--as-mono)", marginLeft: 6 }}>{value}</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {choices.map((c) => (
          <button
            key={c}
            onClick={() => onPick(c)}
            className={`as-chip${c === value ? " active" : ""}`}
            style={{
              padding: "4px 10px",
              border: "1px solid var(--as-line)",
              background: c === value ? "var(--as-accent)" : "var(--as-fill-1)",
              color: c === value ? "#04140e" : "var(--as-ink-1)",
              borderRadius: 4,
              fontSize: 11,
              fontFamily: "var(--as-mono)",
              cursor: "pointer",
            }}
          >
            {c}
          </button>
        ))}
      </div>
    </div>
  );
}
