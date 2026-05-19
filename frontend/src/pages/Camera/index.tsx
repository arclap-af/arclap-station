import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/Button";
import { Pill } from "../../components/Pill";
import { camera, type CameraSettings } from "../../lib/bridge/camera";
import { Viewfinder } from "./Viewfinder";

export function CameraPage() {
  const qc = useQueryClient();
  const [grid, setGrid] = useState<"thirds" | "center" | "none">("thirds");
  const [showHistogram, setShowHistogram] = useState(true);
  const [toast, setToast] = useState<string | null>(null);

  // Pull real choices from gphoto2 instead of hardcoded constants — what
  // shows up below is what the actual camera supports.
  const { data: info } = useQuery({
    queryKey: ["camera.info"],
    queryFn: camera.info,
    refetchInterval: 30_000,
  });
  const MODES = info?.choices.mode ?? [];
  const ISOS = info?.choices.iso ?? [];
  const SHUTTERS = info?.choices.shutter ?? [];
  const APERTURES = info?.choices.aperture ?? [];

  const { data: settings } = useQuery({
    queryKey: ["camera.settings"],
    queryFn: camera.settings,
  });

  const patch = useMutation({
    mutationFn: (p: Partial<CameraSettings>) => camera.updateSettings(p),
    onSuccess: (s) => qc.setQueryData(["camera.settings"], s),
  });
  const capture = useMutation({
    mutationFn: camera.capture,
    onSuccess: (r) => showToast(`Captured ${r.filename}`),
    onError: (err) => showToast(err instanceof Error ? err.message : "Capture failed"),
  });
  const reconnect = useMutation({
    mutationFn: camera.reconnect,
    onSuccess: () => showToast("Camera reconnected"),
  });
  const syncClock = useMutation({ mutationFn: camera.syncClock, onSuccess: () => showToast("Clock synced") });
  const usbReset = useMutation({ mutationFn: camera.usbReset, onSuccess: () => showToast("USB bus reset") });

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  };

  const exp = settings ?? {
    mode: "M",
    iso: "200",
    shutter: "1/250",
    aperture: "f/8",
    ev: "0",
    wb: "Daylight",
    kelvin: 5500,
    drive: "Single",
    quality: "RAW+JPEG L",
    focus: "AF-S",
    af_area: "Center spot",
    metering: "Evaluative",
    picture_style: "Standard",
    color_space: "sRGB",
    aspect: "3:2",
  };

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1340 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14, gap: 14 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <h1 className="as-h1" style={{ margin: 0 }}>Camera</h1>
              <Pill tone="ok">PTP session · live</Pill>
            </div>
            <div className="as-h1-sub" style={{ marginBottom: 0 }}>
              gphoto2 control · viewfinder · {exp.mode} · {exp.shutter}s · {exp.aperture} · ISO {exp.iso}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <Button onClick={() => syncClock.mutate()} disabled={syncClock.isPending}>Sync clock</Button>
            <Button onClick={() => usbReset.mutate()} disabled={usbReset.isPending}>USB reset</Button>
            <Button onClick={() => reconnect.mutate()} disabled={reconnect.isPending}>Reconnect</Button>
            <Link to="/camera/properties" style={{ textDecoration: "none" }}>
              <Button>Properties</Button>
            </Link>
          </div>
        </div>

        <div className="as-card" style={{ padding: 0, overflow: "hidden", marginBottom: 14 }}>
          <Viewfinder grid={grid} showHistogram={showHistogram} />
          {toast && (
            <div
              style={{
                position: "absolute",
                bottom: 54,
                left: "50%",
                transform: "translateX(-50%)",
                padding: "8px 16px",
                borderRadius: 8,
                background: "var(--as-accent)",
                color: "#04140e",
                fontSize: 12.5,
                fontWeight: 700,
                animation: "as-toast 200ms ease-out",
                zIndex: 5,
              }}
            >
              {toast}
            </div>
          )}
          <div style={{ padding: "12px 14px", background: "var(--as-bg-2)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 4 }}>
              {([
                ["thirds", "Grid"],
                ["center", "Center"],
                ["none", "Off"],
              ] as const).map(([id, n]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setGrid(id)}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: `1px solid ${grid === id ? "var(--as-accent)" : "var(--as-line)"}`,
                    background: grid === id ? "var(--as-accent-soft)" : "transparent",
                    color: grid === id ? "var(--as-accent-2)" : "var(--as-ink-3)",
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  {n}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setShowHistogram((v) => !v)}
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: `1px solid ${showHistogram ? "var(--as-accent)" : "var(--as-line)"}`,
                  background: showHistogram ? "var(--as-accent-soft)" : "transparent",
                  color: showHistogram ? "var(--as-accent-2)" : "var(--as-ink-3)",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  marginLeft: 8,
                }}
              >
                Histogram
              </button>
            </div>
            <Button variant="primary" onClick={() => capture.mutate()} disabled={capture.isPending}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--as-bad)" }} />
              {capture.isPending ? "Capturing…" : "Shutter"}
            </Button>
          </div>
        </div>

        <Tile title="Mode">
          <Row>
            {MODES.map((m) => (
              <Chip key={m} active={exp.mode === m} onClick={() => patch.mutate({ mode: m })} large>
                {m}
              </Chip>
            ))}
          </Row>
        </Tile>

        <div className="as-grid-2" style={{ alignItems: "start" }}>
          <Tile title="ISO">
            <Row wrap>
              {ISOS.map((v) => (
                <Chip key={v} active={exp.iso === v} onClick={() => patch.mutate({ iso: v })}>
                  {v}
                </Chip>
              ))}
            </Row>
          </Tile>
          <Tile title="Aperture">
            <Row wrap>
              {APERTURES.map((v) => (
                <Chip key={v} active={exp.aperture === v} onClick={() => patch.mutate({ aperture: v })}>
                  {v}
                </Chip>
              ))}
            </Row>
          </Tile>
          <Tile title="Shutter speed">
            <Row wrap>
              {SHUTTERS.map((v) => (
                <Chip key={v} active={exp.shutter === v} onClick={() => patch.mutate({ shutter: v })}>
                  {v}
                </Chip>
              ))}
            </Row>
          </Tile>
          <Tile title="White balance">
            <select
              className="as-select"
              value={exp.wb}
              onChange={(e) => patch.mutate({ wb: e.target.value })}
              aria-label="White balance"
            >
              {["Auto", "Daylight", "Cloudy", "Shade", "Tungsten", "Fluorescent", "Flash", "Custom Kelvin"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
            <div className="as-field-label" style={{ marginTop: 12 }}>Kelvin · {exp.kelvin} K</div>
            <input
              type="range"
              min={2500}
              max={10000}
              step={100}
              value={exp.kelvin}
              onChange={(e) => patch.mutate({ kelvin: parseInt(e.target.value, 10) })}
              style={{ width: "100%", accentColor: "var(--as-accent)" }}
            />
          </Tile>
        </div>
      </div>
    </div>
  );
}

function Tile({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="as-card" style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 700, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function Row({ children, wrap }: { children: React.ReactNode; wrap?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 4, flexWrap: wrap ? "wrap" : "nowrap" }}>{children}</div>
  );
}

function Chip({ active, onClick, children, large }: { active: boolean; onClick: () => void; children: React.ReactNode; large?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: large ? "8px 14px" : "6px 12px",
        borderRadius: 6,
        border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
        background: active ? "var(--as-accent-soft)" : "var(--as-surface)",
        color: active ? "var(--as-accent-2)" : "var(--as-ink)",
        fontFamily: "var(--as-mono)",
        fontSize: large ? 13 : 12,
        fontWeight: 600,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}
