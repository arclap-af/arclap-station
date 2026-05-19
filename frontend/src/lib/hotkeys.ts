import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { useToast } from "../components/ToastQueue";

// Global keyboard shortcuts for power-user operators.
//
// j/k = next/prev gallery row, c = capture, r = reconnect camera,
// g h = go home, g c = go camera, g s = go schedule, g d = go
// destinations, / = focus search if present, ? = show shortcuts overlay.
//
// We ignore keystrokes inside <input>, <textarea>, [contenteditable]
// so typing into a form field never triggers a shortcut.

export function useGlobalHotkeys() {
  const nav = useNavigate();
  const toast = useToast();

  useEffect(() => {
    let prefix: string | null = null;
    let prefixTimer: ReturnType<typeof setTimeout> | null = null;

    function resetPrefix() {
      prefix = null;
      if (prefixTimer) {
        clearTimeout(prefixTimer);
        prefixTimer = null;
      }
    }

    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
        if (target.isContentEditable) return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      // Two-key sequences (e.g. "g h").
      if (prefix === "g") {
        const dest: Record<string, string> = {
          h: "/home", c: "/camera", s: "/schedule",
          d: "/destinations", g: "/gallery", t: "/terminal",
          n: "/settings",
        };
        const path = dest[e.key];
        if (path) {
          nav(path);
          resetPrefix();
          e.preventDefault();
          return;
        }
        resetPrefix();
      }

      if (e.key === "g") {
        prefix = "g";
        prefixTimer = setTimeout(resetPrefix, 1500);
        return;
      }

      // Single-key shortcuts.
      if (e.key === "?") {
        toast.show(
          "shortcuts: g h home · g c cam · g g gallery · g s schedule · c capture · r reconnect",
          "info", 6000,
        );
        e.preventDefault();
        return;
      }
      if (e.key === "/") {
        const s = document.querySelector('input[placeholder*="Search"]') as HTMLInputElement | null;
        if (s) { s.focus(); e.preventDefault(); }
      }
      if (e.key === "c") {
        // Fire a synthetic click on the page's "Capture" button if visible.
        const btn = Array.from(document.querySelectorAll("button"))
          .find((b) => /capture/i.test(b.textContent ?? ""));
        if (btn instanceof HTMLButtonElement && !btn.disabled) {
          btn.click();
          e.preventDefault();
        }
      }
      if (e.key === "r") {
        const btn = Array.from(document.querySelectorAll("button"))
          .find((b) => /reconnect/i.test(b.textContent ?? ""));
        if (btn instanceof HTMLButtonElement && !btn.disabled) {
          btn.click();
          e.preventDefault();
        }
      }
    }

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      resetPrefix();
    };
  }, [nav, toast]);
}
