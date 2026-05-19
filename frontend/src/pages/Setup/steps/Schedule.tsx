import { FormField, TextInput } from "../../../components/FormField";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

const DAYS: Array<[string, string]> = [
  ["mon", "M"],
  ["tue", "T"],
  ["wed", "W"],
  ["thu", "T"],
  ["fri", "F"],
  ["sat", "S"],
  ["sun", "S"],
];

const INTERVALS = [5, 10, 15, 30, 60] as const;

export function Schedule({ state, update }: Props) {
  const toggleDay = (d: string) =>
    update("schedDays", state.schedDays.includes(d) ? state.schedDays.filter((x) => x !== d) : [...state.schedDays, d]);

  return (
    <>
      <FormField label="Interval">
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {INTERVALS.map((m) => {
            const active = state.schedInterval === m;
            return (
              <button
                key={m}
                type="button"
                onClick={() => update("schedInterval", m)}
                style={{
                  padding: "8px 14px",
                  borderRadius: 8,
                  border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
                  background: active ? "var(--as-accent)" : "transparent",
                  color: active ? "#04140e" : "var(--as-ink)",
                  fontWeight: active ? 700 : 500,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: 13,
                }}
              >
                {m < 60 ? `${m} min` : "1 h"}
              </button>
            );
          })}
        </div>
      </FormField>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <FormField label="From">
          <TextInput type="time" value={state.schedFrom} onChange={(e) => update("schedFrom", e.target.value)} />
        </FormField>
        <FormField label="To">
          <TextInput type="time" value={state.schedTo} onChange={(e) => update("schedTo", e.target.value)} />
        </FormField>
      </div>
      <FormField label="Days" className="!mb-0">
        <div style={{ display: "flex", gap: 6 }}>
          {DAYS.map(([d, l]) => {
            const active = state.schedDays.includes(d);
            return (
              <button
                key={d}
                type="button"
                onClick={() => toggleDay(d)}
                style={{
                  flex: 1,
                  height: 40,
                  borderRadius: 8,
                  border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
                  background: active ? "var(--as-accent-soft)" : "transparent",
                  color: active ? "var(--as-accent-2)" : "var(--as-ink-3)",
                  fontWeight: 700,
                  fontSize: 13,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                {l}
              </button>
            );
          })}
        </div>
      </FormField>
    </>
  );
}
