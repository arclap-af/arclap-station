import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Icon, I } from "../../../components/icons";
import { setup } from "../../../lib/bridge/setup";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

export function CameraStep({ state, update }: Props) {
  const detect = useMutation({
    mutationFn: setup.detectCamera,
    onSuccess: (r) => {
      if (r.detected) {
        update("cameraDetected", true);
        update("cameraModel", r.model ?? "Camera");
        update("cameraLens", r.lens ?? null);
        update("cameraFirmware", r.firmware ?? null);
        update("cameraBattery", r.battery ?? null);
        update("cameraShutterCount", r.shutter_count ?? null);
      }
    },
  });

  useEffect(() => {
    if (state.cameraDetected || detect.isPending) return;
    detect.mutate();
    const interval = setInterval(() => {
      if (!state.cameraDetected) detect.mutate();
    }, 3000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.cameraDetected]);

  return (
    <div style={{ textAlign: "center" }}>
      {!state.cameraDetected ? (
        <>
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              border: "3px solid var(--as-accent)",
              borderTopColor: "transparent",
              margin: "0 auto 18px",
              animation: "as-spin-rot 1s linear infinite",
            }}
          />
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>Scanning USB bus…</div>
          <div style={{ fontSize: 12.5, color: "var(--as-ink-3)", marginBottom: 18 }}>
            Plug the DSLR via USB-C and switch to PC/PTP mode
          </div>
          <div
            className="mono"
            style={{
              padding: "8px 12px",
              background: "var(--as-bg-2)",
              borderRadius: 6,
              color: "var(--as-ink-4)",
              fontSize: 11,
            }}
          >
            $ gphoto2 --auto-detect
          </div>
        </>
      ) : (
        <>
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              background: "var(--as-accent)",
              color: "#04140e",
              margin: "0 auto 18px",
              display: "grid",
              placeItems: "center",
            }}
          >
            <Icon d={I.check} size={36} stroke={2.5} />
          </div>
          <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>
            {state.cameraModel ?? "Camera detected"}
          </div>
          {state.cameraLens && (
            <div style={{ fontSize: 12, color: "var(--as-ink-3)", marginBottom: 18, fontFamily: "var(--as-mono)" }}>
              {state.cameraLens}
            </div>
          )}
          <div
            style={{
              padding: "12px 16px",
              background: "var(--as-bg-2)",
              borderRadius: 8,
              fontSize: 12,
              lineHeight: 1.7,
              textAlign: "left",
            }}
          >
            {state.cameraFirmware && (
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--as-ink-3)" }}>Firmware</span>
                <span className="mono">{state.cameraFirmware}</span>
              </div>
            )}
            {state.cameraBattery !== null && (
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--as-ink-3)" }}>Battery</span>
                <span className="mono">{state.cameraBattery}%</span>
              </div>
            )}
            {state.cameraShutterCount !== null && (
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--as-ink-3)" }}>Shutter</span>
                <span className="mono">{state.cameraShutterCount.toLocaleString()}</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
