import { useEffect, useRef } from "react";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

export function PIN({ state, update }: Props) {
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    refs.current[0]?.focus();
  }, []);

  const setDigit = (i: number, raw: string) => {
    const v = raw.replace(/\D/g, "").slice(-1);
    const next = [...state.pin];
    next[i] = v;
    update("pin", next);
    if (v && i < 5) refs.current[i + 1]?.focus();
  };

  const onKey = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !state.pin[i] && i > 0) refs.current[i - 1]?.focus();
  };

  return (
    <>
      <div style={{ fontSize: 13, color: "var(--as-ink-3)", marginBottom: 18, textAlign: "center" }}>
        Pick a 6-digit PIN. You&apos;ll be asked for it on every login.
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "center", marginBottom: 14 }}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <input
            key={i}
            ref={(el) => {
              refs.current[i] = el;
            }}
            className="as-input mono"
            style={{ width: 54, height: 64, fontSize: 26, textAlign: "center", padding: 0 }}
            maxLength={1}
            type="password"
            inputMode="numeric"
            autoComplete="off"
            aria-label={`PIN digit ${i + 1}`}
            value={state.pin[i]}
            onChange={(e) => setDigit(i, e.target.value)}
            onKeyDown={(e) => onKey(i, e)}
          />
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--as-ink-4)", textAlign: "center" }}>
        Recovery requires physical access to the Pi · don&apos;t reuse from another device
      </div>
    </>
  );
}
