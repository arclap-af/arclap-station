import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

// Toast queue — replaces the old single-string toast state we had
// scattered across pages. Show multiple toasts stacked top-right;
// each auto-dismisses after its TTL.
//
// Usage:
//   const toast = useToast();
//   toast.show("Captured 1/2500s f/8 ISO 400", "ok");
//   toast.show(err.message, "bad");

export type ToastLevel = "ok" | "warn" | "bad" | "info";

interface ToastItem {
  id: number;
  level: ToastLevel;
  text: string;
  ttlMs: number;
  createdAt: number;
}

interface ToastApi {
  show: (text: string, level?: ToastLevel, ttlMs?: number) => void;
  clear: () => void;
}

const Ctx = createContext<ToastApi | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const show = useCallback((text: string, level: ToastLevel = "info", ttlMs = 3500) => {
    const id = nextId++;
    setItems((prev) => [...prev.slice(-4), { id, level, text, ttlMs, createdAt: Date.now() }]);
  }, []);

  const clear = useCallback(() => setItems([]), []);

  // Sweep expired toasts every 200ms (cheap).
  useEffect(() => {
    if (items.length === 0) return;
    const t = setInterval(() => {
      const now = Date.now();
      setItems((prev) => prev.filter((it) => now - it.createdAt < it.ttlMs));
    }, 200);
    return () => clearInterval(t);
  }, [items.length]);

  return (
    <Ctx.Provider value={{ show, clear }}>
      {children}
      <div
        aria-live="polite"
        style={{
          position: "fixed",
          top: 16,
          right: 16,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          zIndex: 9999,
          pointerEvents: "none",
        }}
      >
        {items.map((it) => (
          <div
            key={it.id}
            className={`as-toast as-toast-${it.level}`}
            style={{
              background: "var(--as-surface-2)",
              border: "1px solid var(--as-line-2)",
              borderLeft: `3px solid var(--as-${it.level === "info" ? "ink-3" : it.level})`,
              borderRadius: 8,
              padding: "10px 14px",
              minWidth: 220,
              maxWidth: 380,
              fontSize: 13,
              boxShadow: "var(--as-shadow-2)",
              pointerEvents: "auto",
              animation: "as-toast-in 180ms ease-out",
            }}
            onClick={() => setItems((prev) => prev.filter((x) => x.id !== it.id))}
          >
            {it.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const v = useContext(Ctx);
  if (!v) {
    // Defensive fallback so a page outside the provider doesn't crash.
    return { show: () => {}, clear: () => {} };
  }
  return v;
}
