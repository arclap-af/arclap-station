import type { Config } from "tailwindcss";

// Tokens are the verbatim values from /tmp/design-v3/.../arclap-station.css :root.
// Tailwind classes (`bg-as-bg`, `text-as-accent-2`, etc.) map 1:1.
// Non-Tailwind code can still read them as CSS variables via styles/tokens.css.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "as-bg": "#0a0e14",
        "as-bg-2": "#0f141c",
        "as-surface": "#131922",
        "as-surface-2": "#1a2230",
        "as-line": "#1f2937",
        "as-line-2": "#2a3548",
        "as-ink": "#e8eef8",
        "as-ink-2": "#c1cad8",
        "as-ink-3": "#8a96a8",
        "as-ink-4": "#5a6678",
        "as-accent": "#10b981",
        "as-accent-2": "#34d399",
        "as-warn": "#f59e0b",
        "as-bad": "#ef4444",
        "as-blue": "#3b82f6",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
