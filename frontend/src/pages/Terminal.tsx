import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../components/Button";
import { terminal } from "../lib/bridge/terminal";
import { useWebSocket } from "../lib/ws";

const PRESETS: Record<"shell" | "gphoto2", Array<[string, string]>> = {
  shell: [
    ["systemctl status arclap-station", "Service status"],
    ["systemctl restart arclap-station", "Restart service"],
    ["journalctl -u arclap-station -n 20", "Recent logs"],
    ["df -h /media/sdcard", "SD card usage"],
    ["lsusb", "USB devices"],
    ["ip a", "Network interfaces"],
    ["vcgencmd measure_temp", "Pi temperature"],
    ["sudo systemctl list-timers --all", "Scheduled tasks"],
  ],
  gphoto2: [
    ["gphoto2 --auto-detect", "List connected cameras"],
    ["gphoto2 --summary", "Camera info"],
    ["gphoto2 --list-config", "All settable properties"],
    ["gphoto2 --get-config /main/imgsettings/iso", "Read ISO"],
    ["gphoto2 --capture-preview", "Live view frame"],
    ["gphoto2 --reset", "USB reset"],
  ],
};

const decoder = new TextDecoder();

// Strip ANSI CSI escape sequences from PTY output. We don't render a full
// xterm here — that needs xterm.js — so we just drop the cursor/color codes
// to keep the output readable.
const ANSI_RE =
  // eslint-disable-next-line no-control-regex
  /\x1b(?:\[[0-?]*[ -/]*[@-~]|\]\d+;[^\x07]*\x07|[@-_])/g;

const MAX_BUFFER = 200_000; // chars; well past one screen of output

export function Terminal() {
  // Single buffer string makes scrollback feel like a real terminal —
  // line breaks happen visually via white-space:pre-wrap.
  const [buffer, setBuffer] = useState<string>(
    "Arclap Station restricted shell\nType a command and press Enter. Press preset on the right to insert.\n",
  );
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"shell" | "gphoto2">("shell");
  const cmdHistory = useRef<string[]>([]);
  const histIdx = useRef(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const onMessage = useCallback((ev: MessageEvent) => {
    const raw =
      typeof ev.data === "string"
        ? ev.data
        : ev.data instanceof ArrayBuffer
          ? decoder.decode(ev.data)
          : decoder.decode(new Uint8Array(ev.data as ArrayBuffer));
    if (!raw) return;
    const clean = raw.replace(/\r\n?/g, "\n").replace(ANSI_RE, "");
    setBuffer((prev) => {
      const next = prev + clean;
      return next.length > MAX_BUFFER ? next.slice(-MAX_BUFFER) : next;
    });
  }, []);

  const { status, send } = useWebSocket(terminal.url(), onMessage, {
    binaryType: "arraybuffer",
  });

  // Auto-scroll to bottom on new output.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [buffer]);

  // Send a resize message once the WS opens so `top`/`htop` don't wrap weirdly.
  useEffect(() => {
    if (status === "open") {
      send(JSON.stringify({ type: "resize", rows: 28, cols: 110 }));
    }
  }, [status, send]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [tab]);

  function runCommand(cmd: string) {
    if (status !== "open") return;
    if (cmd) {
      cmdHistory.current.push(cmd);
      histIdx.current = -1;
    }
    send(cmd + "\n");
    setInput("");
  }

  function sendCtrlC() {
    if (status !== "open") return;
    // 0x03 = ETX = Ctrl+C. PTY interprets it as SIGINT to the foreground process.
    send(new Uint8Array([0x03]).buffer);
    setInput("");
  }

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runCommand(input);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistory.current.length === 0) return;
      const ni =
        histIdx.current < 0
          ? cmdHistory.current.length - 1
          : Math.max(0, histIdx.current - 1);
      histIdx.current = ni;
      setInput(cmdHistory.current[ni]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdx.current < 0) return;
      const ni = histIdx.current + 1;
      if (ni >= cmdHistory.current.length) {
        histIdx.current = -1;
        setInput("");
      } else {
        histIdx.current = ni;
        setInput(cmdHistory.current[ni]);
      }
    } else if (e.key === "c" && e.ctrlKey) {
      // Ctrl+C with non-empty input → clear input. Empty input → SIGINT.
      e.preventDefault();
      if (input) setInput("");
      else sendCtrlC();
    } else if (e.key === "l" && e.ctrlKey) {
      e.preventDefault();
      setBuffer("");
    } else if (e.key === "d" && e.ctrlKey) {
      e.preventDefault();
      if (!input) {
        // 0x04 = EOT = Ctrl+D → close shell
        send(new Uint8Array([0x04]).buffer);
      }
    }
  };

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1300 }}>
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
              Restricted PTY · runs as <span className="mono">arclap</span> · sudo blocked ·
              Ctrl+C interrupt · Ctrl+L clear · Ctrl+D exit
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button onClick={() => setBuffer("")}>Clear</Button>
            <Button
              onClick={() => {
                const blob = new Blob([buffer], { type: "text/plain" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `shell-${Date.now()}.log`;
                a.click();
                setTimeout(() => URL.revokeObjectURL(url), 500);
              }}
            >
              Export session
            </Button>
          </div>
        </div>

        <div className="as-grid-2" style={{ alignItems: "start", gap: 14 }}>
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
              <span style={{ display: "inline-flex", gap: 5 }}>
                {["#ef4444", "#f59e0b", "#10b981"].map((c, i) => (
                  <span
                    key={i}
                    style={{ width: 11, height: 11, borderRadius: "50%", background: c }}
                  />
                ))}
              </span>
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: "var(--as-ink-3)",
                  flex: 1,
                  textAlign: "center",
                }}
              >
                arclap@station: ~
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
              ref={scrollRef}
              role="log"
              aria-live="polite"
              style={{
                height: 520,
                overflowY: "auto",
                padding: "12px 14px",
                background: "#06090d",
                fontFamily: "var(--as-mono)",
                fontSize: 12,
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "var(--as-ink)",
              }}
              onClick={() => inputRef.current?.focus()}
            >
              {buffer}
            </div>
            <div
              style={{
                padding: "8px 12px",
                background: "#06090d",
                borderTop: "1px solid var(--as-line)",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <span
                className="mono"
                style={{ color: "var(--as-accent)", fontSize: 12 }}
              >
                $
              </span>
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKey}
                disabled={status !== "open"}
                aria-label="Shell input"
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: "var(--as-ink)",
                  fontFamily: "var(--as-mono)",
                  fontSize: 12,
                  padding: 0,
                }}
                placeholder={
                  status === "open"
                    ? "Type a command, ↑ history, Ctrl+C interrupt, Ctrl+L clear"
                    : status === "connecting"
                      ? "Connecting…"
                      : "Disconnected — refresh the page"
                }
                autoFocus
                autoComplete="off"
                spellCheck={false}
              />
              <Button
                type="button"
                onClick={sendCtrlC}
                disabled={status !== "open"}
                style={{ padding: "4px 10px", fontSize: 10 }}
                title="Send SIGINT (Ctrl+C)"
              >
                Ctrl-C
              </Button>
            </div>
          </div>

          <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
            <div
              className="as-tabs"
              style={{ margin: 0, padding: "0 12px", background: "var(--as-bg-2)" }}
            >
              {(["shell", "gphoto2"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`as-tab ${tab === t ? "active" : ""}`}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            <div style={{ padding: 12, maxHeight: 560, overflowY: "auto" }}>
              {PRESETS[tab].map(([c, d], i) => (
                <div
                  key={i}
                  onClick={() => {
                    setInput(c);
                    inputRef.current?.focus();
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && setInput(c)}
                  style={{
                    padding: "9px 11px",
                    borderRadius: 8,
                    border: "1px solid var(--as-line)",
                    marginBottom: 6,
                    cursor: "pointer",
                    background: "var(--as-bg-2)",
                  }}
                >
                  <div
                    className="mono"
                    style={{
                      fontSize: 11.5,
                      color: "var(--as-accent-2)",
                      marginBottom: 2,
                    }}
                  >
                    {c}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--as-ink-3)" }}>{d}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
