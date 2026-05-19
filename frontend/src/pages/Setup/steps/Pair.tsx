import { FormField, TextInput } from "../../../components/FormField";
import { Icon, I } from "../../../components/icons";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

export function Pair({ state, update }: Props) {
  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 18 }}>
        <button
          type="button"
          onClick={() => update("pair", true)}
          className="as-card"
          style={{
            flex: 1,
            padding: 14,
            cursor: "pointer",
            border: `1px solid ${state.pair ? "var(--as-accent)" : "var(--as-line)"}`,
            background: state.pair ? "var(--as-accent-soft)" : "var(--as-surface)",
            textAlign: "left",
            fontFamily: "inherit",
            color: "inherit",
          }}
        >
          <Icon d={I.cloud} size={20} style={{ color: state.pair ? "var(--as-accent-2)" : "var(--as-ink-3)", marginBottom: 6 }} />
          <div style={{ fontSize: 13, fontWeight: 600 }}>Pair now</div>
          <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginTop: 3 }}>Remote support + customer cockpit</div>
        </button>
        <button
          type="button"
          onClick={() => update("pair", false)}
          className="as-card"
          style={{
            flex: 1,
            padding: 14,
            cursor: "pointer",
            border: `1px solid ${!state.pair ? "var(--as-accent)" : "var(--as-line)"}`,
            background: !state.pair ? "var(--as-accent-soft)" : "var(--as-surface)",
            textAlign: "left",
            fontFamily: "inherit",
            color: "inherit",
          }}
        >
          <Icon d={I.lock} size={20} style={{ color: !state.pair ? "var(--as-accent-2)" : "var(--as-ink-3)", marginBottom: 6 }} />
          <div style={{ fontSize: 13, fontWeight: 600 }}>Stay standalone</div>
          <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginTop: 3 }}>LAN only · no cloud</div>
        </button>
      </div>
      {state.pair && (
        <FormField label="Pair code" className="!mb-0">
          <TextInput className="mono" value={state.pairCode} onChange={(e) => update("pairCode", e.target.value)} />
        </FormField>
      )}
    </>
  );
}
