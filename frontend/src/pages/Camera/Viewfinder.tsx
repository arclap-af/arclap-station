import { useEffect, useRef, useState } from "react";
import { useWebSocket } from "../../lib/ws";

interface ViewfinderProps {
  grid: "thirds" | "center" | "none";
  showHistogram: boolean;
}

export function Viewfinder({ grid, showHistogram }: ViewfinderProps) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [fps, setFps] = useState(0);
  const counterRef = useRef({ count: 0, last: performance.now(), url: null as string | null });

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
  };

  const { status } = useWebSocket("/api/camera/liveview", onFrame, { binaryType: "blob" });

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
      {showHistogram && (
        <div style={{ position: "absolute", top: 12, right: 12, padding: 6, borderRadius: 6, background: "rgba(0,0,0,0.7)", width: 140, height: 60 }}>
          <svg width="100%" height="100%" viewBox="0 0 100 40" preserveAspectRatio="none">
            {[3, 8, 16, 28, 35, 32, 24, 18, 12, 8, 5, 3, 2, 1].map((v, i, a) => {
              const x = i * (100 / a.length);
              const w = 100 / a.length;
              return <rect key={i} x={x} y={40 - v} width={w - 0.5} height={v} fill="rgba(255,255,255,0.7)" />;
            })}
          </svg>
        </div>
      )}
    </div>
  );
}
