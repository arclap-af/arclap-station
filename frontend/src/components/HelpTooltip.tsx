import { useState } from "react";

// Hover-only help tooltip. Pass `text` for short messages, or `docUrl`
// to link to the deeper operator runbook (opens in new tab).
//
// Usage: <HelpTooltip text="Stations >90 days without a PIN rotation
//                          should be re-keyed."
//                     docUrl="/docs/operator.md#pin-rotation" />

export function HelpTooltip({
  text,
  docUrl,
  size = 14,
}: { text: string; docUrl?: string; size?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      style={{ position: "relative", display: "inline-flex", alignItems: "center", marginLeft: 4 }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span
        role="button"
        tabIndex={0}
        aria-label="help"
        style={{
          width: size,
          height: size,
          borderRadius: size,
          border: "1px solid var(--as-line)",
          color: "var(--as-ink-3)",
          fontSize: Math.max(9, size - 4),
          fontWeight: 700,
          textAlign: "center",
          lineHeight: `${size - 2}px`,
          cursor: "help",
          userSelect: "none",
        }}
      >
        ?
      </span>
      {open && (
        <div
          role="tooltip"
          style={{
            position: "absolute",
            left: size + 6,
            top: -4,
            minWidth: 220,
            maxWidth: 320,
            padding: "8px 10px",
            background: "var(--as-surface-2)",
            color: "var(--as-ink)",
            border: "1px solid var(--as-line-2)",
            borderRadius: 8,
            fontSize: 12,
            lineHeight: 1.45,
            zIndex: 5000,
            boxShadow: "var(--as-shadow-2)",
          }}
        >
          {text}
          {docUrl && (
            <div style={{ marginTop: 6 }}>
              <a
                href={docUrl}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--as-accent)", fontSize: 11 }}
              >
                Read more →
              </a>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
