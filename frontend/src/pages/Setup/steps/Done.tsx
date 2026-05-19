import { Icon, I } from "../../../components/icons";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
}

export function Done({ state }: Props) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          width: 88,
          height: 88,
          borderRadius: "50%",
          background: "var(--as-accent)",
          color: "#04140e",
          margin: "0 auto 20px",
          display: "grid",
          placeItems: "center",
        }}
      >
        <Icon d={I.check} size={48} stroke={2.5} />
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Setup complete</div>
      <div style={{ fontSize: 13, color: "var(--as-ink-3)", marginBottom: 20 }}>
        {state.stationName} is live and capturing. You can close this tab.
      </div>
      <div style={{ padding: 14, background: "var(--as-bg-2)", borderRadius: 8, fontSize: 12, textAlign: "left", lineHeight: 1.8 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--as-ink-3)" }}>Camera</span>
          <span>{state.cameraModel ?? "—"}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--as-ink-3)" }}>Destination</span>
          <span>{state.destName}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--as-ink-3)" }}>Schedule</span>
          <span>
            Every {state.schedInterval} min · {state.schedFrom}–{state.schedTo}
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--as-ink-3)" }}>Cloud</span>
          <span>{state.pair ? "Paired" : "Standalone"}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--as-ink-3)" }}>Acceptance</span>
          <span style={{ color: state.acceptancePassed ? "var(--as-accent-2)" : "var(--as-warn)" }}>
            {state.acceptancePassed ? "Pass" : "Pending"}
          </span>
        </div>
      </div>
    </div>
  );
}
