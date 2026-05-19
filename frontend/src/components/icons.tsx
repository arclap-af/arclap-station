/**
 * Inline SVG icons matching the design's `I.{check, close, zap, ...}` set.
 * The design embedded path strings; we wrap them in a typed component.
 */
import type { CSSProperties } from "react";

export const I = {
  check: "M5 12l5 5L20 7",
  close: "M6 6l12 12 M18 6L6 18",
  zap: "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 2",
  cloud: "M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M17 8l-5-5-5 5 M12 3v12",
  lock: "M19 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2z M7 11V7a5 5 0 0 1 10 0v4",
  arrowR: "M5 12h14 M12 5l7 7-7 7",
  refresh: "M23 4v6h-6 M1 20v-6h6 M3.51 9a9 9 0 0 1 14.85-3.36L23 10 M20.49 15a9 9 0 0 1-14.85 3.36L1 14",
  plus: "M12 5v14 M5 12h14",
  home: "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M9 22V12h6v10",
  camera:
    "M23 19V8a2 2 0 0 0-2-2h-3.2l-1.8-2h-6l-1.8 2H5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  gallery: "M21 15l-5-5L5 21 M3 5h18v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 5v14 M16 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  schedule: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 2",
  terminal: "M4 17l6-6-6-6 M12 19h8",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  search:
    "M21 21l-4.35-4.35 M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14z",
  sdCard:
    "M19 22H5a2 2 0 0 1-2-2V8l5-6h11a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2z M9 6v4 M13 6v4 M17 6v4",
} as const;

export type IconName = keyof typeof I;

interface IconProps {
  d: string;
  size?: number;
  stroke?: number;
  className?: string;
  style?: CSSProperties;
}

export function Icon({ d, size = 18, stroke = 1.8, className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {d.split(/\sM/).map((p, i) => (
        <path key={i} d={i === 0 ? p : `M${p}`} />
      ))}
    </svg>
  );
}
