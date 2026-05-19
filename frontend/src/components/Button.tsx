import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "icon";
  children?: ReactNode;
}

export function Button({ variant = "ghost", className = "", children, ...rest }: ButtonProps) {
  const cls =
    variant === "icon"
      ? `as-btn-icon ${className}`
      : `as-btn ${variant === "primary" ? "as-btn-primary" : "as-btn-ghost"} ${className}`;
  return (
    <button className={cls.trim()} {...rest}>
      {children}
    </button>
  );
}
