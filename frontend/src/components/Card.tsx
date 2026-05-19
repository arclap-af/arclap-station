import type { CSSProperties, ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  padding?: number;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
}

export function Card({ title, sub, right, children, padding = 18, className = "", style, onClick }: CardProps) {
  return (
    <div className={`as-card ${className}`} style={{ padding, ...style }} onClick={onClick}>
      {(title || right) && (
        <div className="as-card-head">
          {title && (
            <div>
              <div className="as-card-title">{title}</div>
              {sub && <div className="as-card-sub">{sub}</div>}
            </div>
          )}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}
