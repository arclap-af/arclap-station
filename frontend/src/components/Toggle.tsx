interface ToggleProps {
  on: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}

export function Toggle({ on, onChange, label, disabled }: ToggleProps) {
  const click = () => {
    if (!disabled) onChange(!on);
  };
  return (
    <button
      type="button"
      className="as-toggle"
      onClick={click}
      aria-pressed={on}
      aria-label={label ?? "Toggle"}
      style={{ background: "transparent", border: "none", padding: 0, opacity: disabled ? 0.5 : 1 }}
      disabled={disabled}
    >
      <span className={`as-toggle-track ${on ? "on" : ""}`}>
        <span className="as-toggle-thumb" />
      </span>
      {label && <span style={{ fontSize: 12.5, color: "var(--as-ink-2)" }}>{label}</span>}
    </button>
  );
}
