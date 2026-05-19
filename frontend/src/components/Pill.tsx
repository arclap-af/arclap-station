import type { ReactNode } from "react";

interface PillProps {
  tone?: "ok" | "warn" | "bad" | "gray";
  children: ReactNode;
  className?: string;
}

export function Pill({ tone = "gray", children, className = "" }: PillProps) {
  return <span className={`as-pill as-pill-${tone} ${className}`.trim()}>{children}</span>;
}

interface StatusDotProps {
  tone?: "ok" | "warn" | "bad" | "off";
}

export function StatusDot({ tone = "off" }: StatusDotProps) {
  return <span className={`as-dot as-dot-${tone}`} />;
}
