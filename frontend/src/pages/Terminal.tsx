import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../components/Button";
import { terminal } from "../lib/bridge/terminal";
import { useWebSocket } from "../lib/ws";

interface Line {
  kind: "in" | "out" | "err" | "sys";
  text: string;
}

const PRESETS: Record<"shell" | "gphoto2", Array<[string, string]>> = {
  shell: [
    ["systemctl status arclap-station", "Service status"],
    ["systemctl restart arclap-station", "Restart service"],
    ["journalctl -fu arclap-station", "Tail logs"],
    ["df -h /media/sdcard", "SD card usage"],
    ["lsusb", "USB devices"],
    ["ip a", "Network interfaces"],
    ["vcgencmd measure_temp", "Pi temperature"],
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

export function Terminal() {
  const [lines, setLines] = useState<Line[]>([
    { kind: "sys", text: "Arclap Station shell · gphoto2-aware · sudo blocked, /etc and /usr read-only." },
    { kind: "sys", text: "Type `help` for built-ins. Press preset to insert (run with Enter)." },
  ]);
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"shell" | "gphoto2">("shell");
  const [pendingInput, setPendingInput] = useState(true);
  const cmdHistory = useRef<string[]>([]);
  const histIdx = useRef(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const onMessage = useCallback((ev: MessageEvent) => {
    const text =
      typeof ev.data === "string"
        ? ev.data
        : ev.data instanceof ArrayBuffer
          ? decoder.decode(ev.data)
          : decoder.decode(new Uint8Array(ev.data as ArrayBuffer));
    if (!text) return;
    setLines((prev) => [...prev, { kind: "out", text: text.replace(/\r\n?/g, "\n") }]);
    setPendingInput(true);
  }, []);

  const { status, send } = useWebSocket(terminal.url(), onMessage, { binaryType: "arraybuffer" });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [tab]);

  const submit = (cmd: string) => {
    if (!cmd.trim() || status !== "open") return;
    cmdHistory.current.push(cmd);
    histIdx.current = -1;
    setLines((prev) => [...prev, { kind: "in", text: cmd }]);
    send(cmd + "\n");
    setInput("");
    setPendingInput(false);
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") submit(input);
    else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistory.current.length === 0) return;
      const ni = histIdx.current < 0 ? cmdHistory.current.length - 1 : Math.max(0, histIdx.current - 1);
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
    } else if (e.key === "l" && e.ctrlKey) {
      e.preventDefault();
      setLines([]);
    }
  };

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1300 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14 }}>
          <div>
            <h1 className="as-h1">Terminal</h1>
            <div className="as-h1-sub">Restricted PTY · runs as <span className="mono">arclap</span> · sudo blocked</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button onClick={() => setLines([])}>Clear</Button>
            <Button
              onClick={() => {
                const blob = new Blob([lines.map((l) => l.text).join("\n")], { type: "text/plain" });
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
            <div style={{ padding: "8px 14px", background: "#06090d", borderBottom: "1px solid var(--as-line)", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ display: "inline-flex", gap: 5 }}>
                {["#ef4444", "#f59e0b", "#10b981"].map((c, i) => (
                  <span key={i} style={{ width: 11, height: 11, borderRadius: "50%", background: c }} />
                ))}
              </span>
              <div className="mono" style={{ fontSize: 11, color: "var(--as-ink-3)", flex: 1, textAlign: "center" }}>
                arclap@station: ~
              </div>
              <span className="mono" style={{ fontSize: 10, color: "var(--as-ink-4)" }}>
                {status === "open" ? "● connected" : status === "connecting" ? "○ connecting" : "○ offline"}
              </span>
            </div>
            <div
              ref={scrollRef}
              role="log"
              aria-live="polite"
              style={{ height: 520, overflowY: "auto", padding: "12px 14px", background: "#06090d", fontFamily: "var(--as-mono)", fontSize: 12, lineHeight: 1.6 }}
            >
              {lines.map((h, i) => (
                <div
                  key={i}
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    color:
                      h.kind === "in"
                        ? "var(--as-ink)"
                        : h.kind === "err"
                          ? "var(--as-bad)"
                          : h.kind === "sys"
                            ? "var(--as-ink-3)"
                            : "var(--as-accent-2)",
                  }}
                >
                  {h.kind === "in" && <span style={{ color: "var(--as-accent)", marginRight: 6 }}>arclap@station $</span>}
                  {h.text}
                </div>
              ))}
              <div style={{ display: "flex", marginTop: 4, alignItems: "center" }}>
                <span style={{ color: "var(--as-accent)", marginRight: 6, flexShrink: 0 }}>arclap@station $</span>
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKey}
                  disabled={status !== "open" || !pendingInput}
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
                  placeholder={status === "open" ? "Type a command, ↑ history, Ctrl+L clear" : "Connecting…"}
                />
              </div>
            </div>
          </div>

          <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="as-tabs" style={{ margin: 0, padding: "0 12px", background: "var(--as-bg-2)" }}>
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
                  <div className="mono" style={{ fontSize: 11.5, color: "var(--as-accent-2)", marginBottom: 2 }}>{c}</div>
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
