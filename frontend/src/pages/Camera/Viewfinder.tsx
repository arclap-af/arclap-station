import { useEffect, useRef, useState } from "react";
import { useWebSocket } from "../../lib/ws";

interface ViewfinderProps {
  grid: "thirds" | "center" | "none";
  showHistogram: boolean;
}

export function Viewfinder({ grid, showHistogram }: ViewfinderProps) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [fps, setFps] = useState(0);
  // RGB histogram: each channel is a 32-bin array.
  const [hist, setHist] = useState<{ r: number[]; g: number[]; b: number[]; l: number[] } | null>(null);
  const counterRef = useRef({ count: 0, last: performance.now(), url: null as string | null });
  // Throttle histogram recompute so we don't tax the Pi 5's browser CPU.
  const lastHistRef = useRef(0);

  const onFrame = (ev: MessageEvent) => {
    if (!(ev.data instanceof Blob)) return;
    const next = URL.createObjectURL(ev.data);
    const previous = counterRef.current.url;
    counterRef.current.url = next;
    if (imgRef.current) imgRef.current.src = next;
    if (previous) URL.revokeObjectURL(previous);
    counterRef.current.count += 1;
    const now = performance.now();
    if (now - counterRef.current.last > 1000) {
      setFps(Math.round((counterRef.current.count * 1000) / (now - counterRef.current.last)));
      counterRef.current.count = 0;
      counterRef.current.last = now;
    }
    // Recompute histogram from the live frame at ~2 Hz.
    if (showHistogram && now - lastHistRef.current > 500) {
      lastHistRef.current = now;
      computeHistogram(ev.data).then((bins) => bins && setHist(bins));
    }
  };

  // Backend WS lives at /api/camera/preview-ws (see api/camera.py).
  const { status } = useWebSocket("/api/camera/preview-ws", onFrame, { binaryType: "blob" });

  useEffect(
    () => () => {
      if (counterRef.current.url) URL.revokeObjectURL(counterRef.current.url);
    },
    [],
  );

  return (
    <div className="as-cam-viewer" style={{ aspectRatio: "3/2", minHeight: 0 }}>
      <img
        ref={imgRef}
        alt="Live view"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
      />
      {grid === "thirds" && (
        <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
          <line x1="33.33%" y1="0" x2="33.33%" y2="100%" stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
          <line x1="66.66%" y1="0" x2="66.66%" y2="100%" stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
          <line x1="0" y1="33.33%" x2="100%" y2="33.33%" stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
          <line x1="0" y1="66.66%" x2="100%" y2="66.66%" stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
        </svg>
      )}
      {grid === "center" && (
        <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
          <line x1="50%" y1="0" x2="50%" y2="100%" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
          <line x1="0" y1="50%" x2="100%" y2="50%" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
        </svg>
      )}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 42,
          height: 42,
          border: "1.5px solid var(--as-accent)",
          borderRadius: 2,
          pointerEvents: "none",
        }}
      />
      <div style={{ position: "absolute", top: 12, left: 12, display: "flex", gap: 6 }}>
        <span
          style={{
            padding: "4px 9px",
            borderRadius: 12,
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
            fontSize: 10.5,
            fontFamily: "var(--as-mono)",
            display: "flex",
            alignItems: "center",
            gap: 5,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: status === "open" ? "var(--as-accent)" : "var(--as-bad)",
              animation: "as-pulse 1.6s infinite",
            }}
          />
          {status === "open" ? "LIVE" : "OFFLINE"}
        </span>
        <span
          style={{
            padding: "4px 9px",
            borderRadius: 12,
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
            fontSize: 10.5,
            fontFamily: "var(--as-mono)",
          }}
        >
          {fps} fps
        </span>
      </div>
      {showHistogram && hist && (
        <div
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            padding: 6,
            borderRadius: 6,
            background: "rgba(0,0,0,0.7)",
            width: 160,
            height: 70,
          }}
        >
          <svg width="100%" height="100%" viewBox="0 0 100 50" preserveAspectRatio="none">
            {/* Each channel drawn translucent so overlap shows neutral
                grey (= no clipping); red-only / blue-only spikes pop. */}
            {(["r", "g", "b"] as const).map((ch) => (
              <g key={ch}>
                {hist[ch].map((v, i, a) => {
                  const x = i * (100 / a.length);
                  const w = 100 / a.length;
                  const h = Math.max(0, Math.min(50, v));
                  const fill = ch === "r"
                    ? "rgba(255,80,80,0.55)"
                    : ch === "g"
                    ? "rgba(80,255,120,0.55)"
                    : "rgba(120,150,255,0.55)";
                  return (
                    <rect
                      key={`${ch}${i}`}
                      x={x}
                      y={50 - h}
                      width={w - 0.3}
                      height={h}
                      fill={fill}
                    />
                  );
                })}
              </g>
            ))}
            {/* Clipping indicators — tiny red bars at left/right edges
                when bin 0 (under) or bin 31 (over) is saturated. */}
            {hist.r[31] >= 45 && <rect x="99" y="0" width="1.2" height="50" fill="#ff3030" />}
            {hist.r[0] >= 45 && <rect x="0" y="0" width="1.2" height="50" fill="#3030ff" />}
          </svg>
        </div>
      )}
    </div>
  );
}

// Compute 32-bin R/G/B/luma histograms from the live preview JPEG.
// Returns scaled bar heights (0-50 range to match the SVG viewBox).
// Best-effort: null if decoding fails (older browsers, broken JPEG).
async function computeHistogram(blob: Blob): Promise<{
  r: number[]; g: number[]; b: number[]; l: number[];
} | null> {
  try {
    const bitmap = await createImageBitmap(blob);
    const W = 64;
    const H = Math.max(1, Math.round((bitmap.height / bitmap.width) * W));
    const canvas = new OffscreenCanvas(W, H);
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, W, H);
    const data = ctx.getImageData(0, 0, W, H).data;
    const r = new Array(32).fill(0);
    const g = new Array(32).fill(0);
    const b = new Array(32).fill(0);
    const l = new Array(32).fill(0);
    for (let i = 0; i < data.length; i += 4) {
      r[Math.min(31, data[i] >> 3)]++;
      g[Math.min(31, data[i + 1] >> 3)]++;
      b[Math.min(31, data[i + 2] >> 3)]++;
      const y = (0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]) | 0;
      l[Math.min(31, y >> 3)]++;
    }
    const maxAll = Math.max(1, ...r, ...g, ...b);
    const scale = (bins: number[]) => bins.map((v) => Math.round((v / maxAll) * 50));
    return { r: scale(r), g: scale(g), b: scale(b), l: scale(l) };
  } catch {
    return null;
  }
}
