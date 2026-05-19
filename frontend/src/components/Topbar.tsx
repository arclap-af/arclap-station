import type { ReactNode } from "react";
import { Icon, I } from "./icons";

interface TopbarProps {
  onSearch?: (q: string) => void;
  right?: ReactNode;
  placeholder?: string;
}

export function Topbar({ onSearch, right, placeholder = "Search…" }: TopbarProps) {
  return (
    <div className="as-topbar">
      <div className="as-search">
        <Icon d={I.search} size={14} style={{ color: "var(--as-ink-3)" }} />
        <input
          placeholder={placeholder}
          onChange={(e) => onSearch?.(e.target.value)}
          aria-label="Search"
        />
      </div>
      {right}
    </div>
  );
}
