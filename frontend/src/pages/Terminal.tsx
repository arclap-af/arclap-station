import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

import { Button } from "../components/Button";
import { terminal } from "../lib/bridge/terminal";
import { useWebSocket } from "../lib/ws";

// v0.8.2 — replaced the home-rolled buffer + ANSI-strip with a real
// xterm.js terminal: full colour, scrollback, copy/paste, arrow-key
// history (handled by the remote bash), Ctrl-C / Ctrl-D / Ctrl-L, etc.
// The backend is now plain interactive bash (not `--restricted`) running
// as the unprivileged arclap user with sudo blocked.

const PRESETS: Array<{ group: string; rows: Array<[string, string]> }> = [
  {
    group: "Station",
    rows: [
      ["status", "Service status (arclap-station)"],
      ["logs", "Recent journal (50 lines)"],
      ["tailog", "Live journal (Ctrl-C to stop)"],
      ["timers", "All systemd timers"],
      ["temp", "CPU temperature"],
      ["arclap-station support-bundle", "Generate support .tar.gz"],
      ["arclap-station db-integrity", "Run PRAGMA integrity_check"],
    ],
  },
  {
    group: "Camera",
    rows: [
      ["cam", "Auto-detect cameras"],
      ["gphoto2 --summary", "Camera info dump"],
      ["gphoto2 --get-config /main/imgsettings/iso", "Read ISO"],
      ["gphoto2 --reset", "USB reset"],
      ["usb", "lsusb"],
    ],
  },
  {
    group: "Storage",
    rows: [
      ["df -h /media/sdcard", "SD card usage"],
      ["du -sh /media/sdcard/photos/*", "Per-day photo dirs"],
      ["photos", "List photos directory"],
      ["ls -la /var/lib/arclap/backups/", "DB backups"],
      ["ls -la /var/lib/arclap/timelapses/", "Pre-rendered timelapses"],
    ],
  },
  {
    group: "Network",
    rows: [
      ["ip -br address", "All interfaces"],
      ["ss -tln", "Listening ports"],
      ["ping -c 3 1.1.1.1", "Internet probe"],
      ["nmcli device wifi list", "WiFi networks"],
    ],
  },
];

export function Terminal() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [resizing, setResizing] = useState(false);

  // Initialise xterm once — destroy on unmount.
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily:
        '"JetBrains Mono", "SF Mono", "Menlo", "Consolas", ui-monospace, monospace',
      fontSize: 13,
      lineHeight: 1.35,
      scrollback: 5000,
      allowProposedApi: true,
      convertEol: true,
      theme: {
        background: "#06090d",
        foreground: "#e8edf3",
        cursor: "#8fffd6",
        cursorAccent: "#06090d",
        selectionBackground: "#374151",
        black: "#0b0f14",
        red: "#f87171",
        green: "#86efac",
        yellow: "#fde68a",
        blue: "#93c5fd",
        magenta: "#f0abfc",
        cyan: "#67e8f9",
        white: "#e5e7eb",
        brightBlack: "#374151",
        brightRed: "#fca5a5",
        brightGreen: "#bbf7d0",
        brightYellow: "#fef3c7",
        brightBlue: "#bfdbfe",
        brightMagenta: "#f5d0fe",
        brightCyan: "#a5f3fc",
        brightWhite: "#f9fafb",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;
    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // WS message handler — pump raw bytes into xterm.
  const onMessage = useCallback((ev: MessageEvent) => {
    const term = termRef.current;
    if (!term) return;
    if (ev.data instanceof ArrayBuffer) {
      term.write(new Uint8Array(ev.data));
    } else if (typeof ev.data === "string") {
      term.write(ev.data);
    } else if (ev.data instanceof Blob) {
      ev.data.arrayBuffer().then((buf) => term.write(new Uint8Array(buf)));
    }
  }, []);

  const { status, send } = useWebSocket(terminal.url(), onMessage, {
    binaryType: "arraybuffer",
  });

  // Wire xterm input → WS (browser keystrokes → bash stdin).
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const disp = term.onData((data) => send(data));
    return () => disp.dispose();
  }, [send]);

  // Tell the backend to resize the PTY whenever the container resizes,
  // so `top` / `htop` / line wrapping all look right.
  useEffect(() => {
    function doResize() {
      const fit = fitRef.current;
      const term = termRef.current;
      if (!fit || !term) return;
      setResizing(true);
      try {
        fit.fit();
        if (status === "open") {
          send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
        }
      } finally {
        setTimeout(() => setResizing(false), 200);
      }
    }
    doResize();
    window.addEventListener("resize", doResize);
    return () => window.removeEventListener("resize", doResize);
  }, [send, status]);

  // Send an initial resize when the WS opens (with a small delay so
  // xterm has measured its container).
  useEffect(() => {
    if (status !== "open") return;
    const t = setTimeout(() => {
      const term = termRef.current;
      if (term) send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    }, 60);
    return () => clearTimeout(t);
  }, [status, send]);

  // Run a command — pretend the user typed it + pressed Enter.
  const runPreset = (cmd: string) => {
    if (status !== "open") return;
    send(cmd + "\n");
    termRef.current?.focus();
  };

  const clear = () => termRef.current?.clear();

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1400 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            marginBottom: 14,
          }}
        >
          <div>
            <h1 className="as-h1">Terminal</h1>
            <div className="as-h1-sub">
              Interactive bash · runs as <span className="mono">arclap</span> ·
              <span className="mono"> sudo </span>blocked ·
              full ANSI colour + scrollback · arrow keys = command history
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button onClick={clear}>Clear screen</Button>
            <Button onClick={() => termRef.current?.paste(termRef.current?.getSelection() ?? "")}>
              Re-run selection
            </Button>
          </div>
        </div>

        <div
          className="as-grid-2"
          style={{ alignItems: "start", gap: 14, gridTemplateColumns: "minmax(0, 1fr) 320px" }}
        >
          <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
            <div
              style={{
                padding: "8px 14px",
                background: "#06090d",
                borderBottom: "1px solid var(--as-line)",
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: "var(--as-ink-3)",
                  flex: 1,
                  textAlign: "left",
                }}
              >
                arclap@station:~ — xterm.js {resizing ? "· resizing" : ""}
              </div>
              <span
                className="mono"
                style={{ fontSize: 10, color: "var(--as-ink-4)" }}
              >
                {status === "open"
                  ? "● connected"
                  : status === "connecting"
                    ? "○ connecting"
                    : "○ offline"}
              </span>
            </div>
            <div
              ref={containerRef}
              style={{
                height: 560,
                background: "#06090d",
                padding: "8px 4px 4px 12px",
              }}
              onClick={() => termRef.current?.focus()}
            />
          </div>

          <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
            <div
              style={{
                padding: "10px 14px",
                background: "var(--as-bg-2)",
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              Quick commands
            </div>
            <div style={{ padding: 12, maxHeight: 560, overflowY: "auto" }}>
              {PRESETS.map((p) => (
                <div key={p.group} style={{ marginBottom: 14 }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: "var(--as-ink-3)",
                      textTransform: "uppercase",
                      letterSpacing: 0.06,
                      marginBottom: 6,
                    }}
                  >
                    {p.group}
                  </div>
                  {p.rows.map(([cmd, desc]) => (
                    <div
                      key={cmd}
                      onClick={() => runPreset(cmd)}
                      style={{
                        padding: "6px 8px",
                        borderRadius: 5,
                        border: "1px solid var(--as-line)",
                        marginBottom: 4,
                        cursor: "pointer",
                        background: "var(--as-fill-1)",
                      }}
                      title={cmd}
                    >
                      <div
                        className="mono"
                        style={{ fontSize: 11.5, color: "var(--as-ink-1)" }}
                      >
                        {cmd}
                      </div>
                      <div style={{ fontSize: 10.5, color: "var(--as-ink-3)" }}>
                        {desc}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
