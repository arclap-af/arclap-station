import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

import { Button } from "../components/Button";
import { terminal } from "../lib/bridge/terminal";
import { useWebSocket } from "../lib/ws";

// xterm.js bash + tabbed command palette. 9 categories with ~80
// ready-to-run operator commands. Search filter at the top spans all
// categories. Click any command to run it in the remote bash; the
// keyboard still works for ad-hoc typing.

type Cat = "arclap" | "gphoto2" | "services" | "journal" | "storage" | "network" | "usb" | "perf" | "sqlite";

interface Cmd { cmd: string; desc: string; cat: Cat }

const CATEGORIES: { id: Cat; label: string; icon: string }[] = [
  { id: "arclap",   label: "arclap",  icon: "🅰" },
  { id: "gphoto2",  label: "gphoto2", icon: "📷" },
  { id: "services", label: "services", icon: "⚙" },
  { id: "journal",  label: "journal", icon: "📜" },
  { id: "storage",  label: "storage", icon: "💾" },
  { id: "network",  label: "network", icon: "🌐" },
  { id: "usb",      label: "usb",     icon: "🔌" },
  { id: "perf",     label: "perf",    icon: "📊" },
  { id: "sqlite",   label: "db",      icon: "🗄" },
];

const COMMANDS: Cmd[] = [
  // arclap-station CLI — every subcommand from main.py
  { cat: "arclap", cmd: "arclap-station version",         desc: "Print station version + exit" },
  { cat: "arclap", cmd: "arclap-station healthcheck",     desc: "Probe local /api/health (exits 0 on ok)" },
  { cat: "arclap", cmd: "arclap-station camera-watchdog", desc: "Run one camera USB watchdog probe" },
  { cat: "arclap", cmd: "arclap-station retention-sweep", desc: "Enforce 4-tier disk-retention policy now" },
  { cat: "arclap", cmd: "arclap-station exif-backfill",   desc: "Re-extract EXIF for photos missing it" },
  { cat: "arclap", cmd: "arclap-station backup",          desc: "Take a gzipped state.db snapshot" },
  { cat: "arclap", cmd: "arclap-station db-integrity",    desc: "PRAGMA integrity_check on state.db" },
  { cat: "arclap", cmd: "arclap-station support-bundle",  desc: "Write a redacted tar.gz of logs + db for support" },
  { cat: "arclap", cmd: "arclap-station timelapse-daily", desc: "Render the last 24h as an MP4 timelapse" },

  // gphoto2 — the workhorse commands
  { cat: "gphoto2", cmd: "gphoto2 --auto-detect",           desc: "List USB-connected cameras" },
  { cat: "gphoto2", cmd: "gphoto2 --summary",               desc: "Full camera info dump (model, lens, battery, shutter count)" },
  { cat: "gphoto2", cmd: "gphoto2 --abilities",             desc: "What the body supports (capture, preview, config)" },
  { cat: "gphoto2", cmd: "gphoto2 --list-config",           desc: "Every settable config path on this camera" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/imgsettings/iso",        desc: "Read current ISO" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/capturesettings/shutterspeed", desc: "Read shutter speed" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/capturesettings/aperture",     desc: "Read aperture" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/status/batterylevel",    desc: "Battery %" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/status/shuttercounter",  desc: "Total shutter actuations" },
  { cat: "gphoto2", cmd: "gphoto2 --get-config /main/settings/capturetarget", desc: "Where captures land (0=RAM, 1=card)" },
  { cat: "gphoto2", cmd: "gphoto2 --capture-preview --filename=/tmp/preview.jpg", desc: "Grab one liveview frame" },
  { cat: "gphoto2", cmd: "gphoto2 --capture-image",         desc: "Trigger shutter (file stays on camera)" },
  { cat: "gphoto2", cmd: "gphoto2 --capture-image-and-download", desc: "Trigger + pull the file off" },
  { cat: "gphoto2", cmd: "gphoto2 --list-files",            desc: "List files on the camera's storage" },
  { cat: "gphoto2", cmd: "gphoto2 --reset",                 desc: "USB-level reset of the camera" },

  // systemd services
  { cat: "services", cmd: "systemctl status arclap-station --no-pager", desc: "Main backend service status" },
  { cat: "services", cmd: "systemctl restart arclap-station",           desc: "Restart the backend" },
  { cat: "services", cmd: "systemctl status caddy --no-pager",          desc: "Reverse proxy status" },
  { cat: "services", cmd: "systemctl list-timers --no-pager",           desc: "All timers + when each fires next" },
  { cat: "services", cmd: "systemctl list-units --type=service --state=active --no-pager | head -20", desc: "Top 20 active services" },
  { cat: "services", cmd: "systemctl is-active arclap-watchdog.timer arclap-camera-watchdog.timer arclap-retention.timer arclap-backup.timer arclap-integrity.timer arclap-timelapse.timer", desc: "Quick liveness across all 6 arclap timers" },
  { cat: "services", cmd: "systemctl status systemd-resolved systemd-timesyncd --no-pager", desc: "DNS + NTP daemons" },
  { cat: "services", cmd: "systemctl status NetworkManager --no-pager", desc: "NetworkManager status" },

  // journalctl
  { cat: "journal", cmd: "journalctl -u arclap-station -n 50 --no-pager",        desc: "Last 50 backend log lines" },
  { cat: "journal", cmd: "journalctl -u arclap-station -f",                       desc: "Live tail of the backend (Ctrl-C to stop)" },
  { cat: "journal", cmd: "journalctl -u arclap-station -p err -n 50 --no-pager", desc: "Backend errors only" },
  { cat: "journal", cmd: "journalctl -u arclap-station --since '1 hour ago' --no-pager | tail -100", desc: "Last hour of backend logs" },
  { cat: "journal", cmd: "journalctl -u arclap-station --since today --no-pager | grep -i camera | tail", desc: "Today's camera-related log lines" },
  { cat: "journal", cmd: "journalctl -u caddy -n 50 --no-pager",                  desc: "Last 50 Caddy log lines (access + cert)" },
  { cat: "journal", cmd: "journalctl -k -n 50 --no-pager",                         desc: "Last 50 kernel log lines" },
  { cat: "journal", cmd: "journalctl --list-boots | head -10",                     desc: "Last 10 boots with timestamps" },
  { cat: "journal", cmd: "journalctl --disk-usage",                                desc: "How much disk journald is using" },

  // storage
  { cat: "storage", cmd: "df -h",                                desc: "Disk usage across all mounts" },
  { cat: "storage", cmd: "df -h /media/sdcard",                  desc: "Photo-volume usage" },
  { cat: "storage", cmd: "ls -la /media/sdcard/photos/ | head",  desc: "Photos directory structure (year/month/day)" },
  { cat: "storage", cmd: "du -sh /media/sdcard/photos/*",        desc: "Per-year photo disk usage" },
  { cat: "storage", cmd: "du -sh /var/lib/arclap/*",             desc: "Per-subdir usage of /var/lib/arclap" },
  { cat: "storage", cmd: "ls -la /var/lib/arclap/backups/",      desc: "Daily DB backups (gzipped)" },
  { cat: "storage", cmd: "ls -la /var/lib/arclap/timelapses/",   desc: "Pre-rendered timelapses" },
  { cat: "storage", cmd: "ls -la /var/lib/arclap/support/",      desc: "Support-bundle archives" },
  { cat: "storage", cmd: "find /media/sdcard/photos -mtime -1 | wc -l", desc: "Count photos from the last 24h" },
  { cat: "storage", cmd: "stat /var/lib/arclap/state.db",        desc: "state.db metadata + size + mtime" },

  // network
  { cat: "network", cmd: "ip -br address",                      desc: "All interface IPs (brief)" },
  { cat: "network", cmd: "ip -br link",                          desc: "Interface state (UP/DOWN)" },
  { cat: "network", cmd: "ip route show",                        desc: "Routing table + default gateway" },
  { cat: "network", cmd: "nmcli connection show",                desc: "All saved NetworkManager profiles" },
  { cat: "network", cmd: "nmcli device wifi list",               desc: "Available WiFi networks" },
  { cat: "network", cmd: "nmcli general status",                 desc: "NetworkManager overview" },
  { cat: "network", cmd: "ss -tln",                              desc: "Listening TCP ports" },
  { cat: "network", cmd: "ss -tn",                               desc: "Established TCP connections" },
  { cat: "network", cmd: "ping -c 3 1.1.1.1",                    desc: "Internet reachability" },
  { cat: "network", cmd: "ping -c 3 $(ip route | awk '/default/{print $3}')", desc: "Ping the default gateway" },
  { cat: "network", cmd: "dig +short cloudflare.com",            desc: "DNS resolution test" },
  { cat: "network", cmd: "resolvectl status",                    desc: "Active DNS resolvers + DoT state" },
  { cat: "network", cmd: "curl -sk https://1.1.1.1/cdn-cgi/trace", desc: "Internet identity + edge POP" },

  // USB
  { cat: "usb", cmd: "lsusb",                                       desc: "All USB devices" },
  { cat: "usb", cmd: "lsusb -t",                                     desc: "USB topology (tree view)" },
  { cat: "usb", cmd: "lsusb -v -d 04a9: 2>/dev/null | head -50",     desc: "Verbose info for any Canon device" },
  { cat: "usb", cmd: "ls -la /sys/bus/usb/devices/ | head",          desc: "Kernel's view of USB devices" },
  { cat: "usb", cmd: "for d in /sys/bus/usb/devices/*; do v=$(cat $d/idVendor 2>/dev/null); p=$(cat $d/idProduct 2>/dev/null); n=$(cat $d/product 2>/dev/null); if [ \"$v\" = \"04a9\" ] || [ \"$v\" = \"04b0\" ]; then echo \"$d $v:$p $n\"; fi; done", desc: "Find any plugged-in DSLR (Canon + Nikon)" },
  { cat: "usb", cmd: "dmesg --since '5 minutes ago' | grep -i usb",  desc: "Recent kernel USB messages" },
  { cat: "usb", cmd: "udevadm monitor --udev",                       desc: "Live udev events (Ctrl-C to stop)" },

  // perf
  { cat: "perf", cmd: "uptime",                                          desc: "Load + uptime" },
  { cat: "perf", cmd: "free -h",                                          desc: "Memory usage" },
  { cat: "perf", cmd: "vcgencmd measure_temp 2>/dev/null || cat /sys/class/thermal/thermal_zone0/temp", desc: "SoC temperature" },
  { cat: "perf", cmd: "vcgencmd get_throttled 2>/dev/null",               desc: "Throttle history (0x0 = healthy)" },
  { cat: "perf", cmd: "top -b -n 1 | head -20",                           desc: "Top processes snapshot" },
  { cat: "perf", cmd: "ps aux --sort=-rss | head -10",                    desc: "Top 10 by RAM" },
  { cat: "perf", cmd: "ps aux --sort=-%cpu | head -10",                   desc: "Top 10 by CPU" },
  { cat: "perf", cmd: "vmstat 1 5",                                       desc: "5-second VM stats sample" },
  { cat: "perf", cmd: "iostat -xz 1 3 2>/dev/null || vmstat -d 1 3",      desc: "Disk I/O stats" },
  { cat: "perf", cmd: "cat /proc/loadavg",                                desc: "Current load averages" },

  // sqlite (state.db)
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT count(*) AS photos FROM photos;'", desc: "Total photo count" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT upload_state, count(*) FROM photos GROUP BY upload_state;'", desc: "Photos by upload state" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT count(*) AS audit_rows FROM audit_log;'", desc: "Audit log size" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT ts, actor, event FROM audit_log ORDER BY id DESC LIMIT 10;'", desc: "Last 10 audit events" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT * FROM timelapses ORDER BY id DESC LIMIT 5;'", desc: "Last 5 timelapses" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT id, name, type, enabled, last_ok_at FROM destinations;'", desc: "Configured destinations" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'SELECT state, count(*) FROM upload_queue GROUP BY state;'", desc: "Upload queue depth by state" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'PRAGMA integrity_check;'", desc: "Quick integrity check" },
  { cat: "sqlite", cmd: "sqlite3 /var/lib/arclap/state.db 'PRAGMA wal_checkpoint(TRUNCATE);'", desc: "Force WAL truncate now" },
];

export function Terminal() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [tab, setTab] = useState<Cat | "all">("arclap");
  const [search, setSearch] = useState("");

  // Filter commands by tab + search.
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return COMMANDS.filter((c) => {
      if (tab !== "all" && c.cat !== tab) return false;
      if (!q) return true;
      return c.cmd.toLowerCase().includes(q) || c.desc.toLowerCase().includes(q);
    });
  }, [tab, search]);

  // Group by category when search is active (lets you see hits across all tabs).
  const grouped = useMemo(() => {
    if (!search.trim()) return null;
    const buckets: Record<string, Cmd[]> = {};
    for (const c of visible) {
      (buckets[c.cat] ??= []).push(c);
    }
    return buckets;
  }, [visible, search]);

  // xterm bootstrap
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", "SF Mono", "Menlo", "Consolas", ui-monospace, monospace',
      fontSize: 13,
      lineHeight: 1.35,
      scrollback: 5000,
      allowProposedApi: true,
      convertEol: true,
      theme: {
        background: "#06090d", foreground: "#e8edf3",
        cursor: "#8fffd6", cursorAccent: "#06090d",
        selectionBackground: "#374151",
        black: "#0b0f14", red: "#f87171", green: "#86efac",
        yellow: "#fde68a", blue: "#93c5fd", magenta: "#f0abfc",
        cyan: "#67e8f9", white: "#e5e7eb",
        brightBlack: "#374151", brightRed: "#fca5a5", brightGreen: "#bbf7d0",
        brightYellow: "#fef3c7", brightBlue: "#bfdbfe", brightMagenta: "#f5d0fe",
        brightCyan: "#a5f3fc", brightWhite: "#f9fafb",
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

  const onMessage = useCallback((ev: MessageEvent) => {
    const term = termRef.current;
    if (!term) return;
    if (ev.data instanceof ArrayBuffer) term.write(new Uint8Array(ev.data));
    else if (typeof ev.data === "string") term.write(ev.data);
    else if (ev.data instanceof Blob) ev.data.arrayBuffer().then((b) => term.write(new Uint8Array(b)));
  }, []);

  const { status, send } = useWebSocket(terminal.url(), onMessage, { binaryType: "arraybuffer" });

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const d = term.onData((data) => send(data));
    return () => d.dispose();
  }, [send]);

  useEffect(() => {
    function doResize() {
      const fit = fitRef.current;
      const term = termRef.current;
      if (!fit || !term) return;
      try {
        fit.fit();
        if (status === "open") send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
      } catch {/* ignore */}
    }
    doResize();
    window.addEventListener("resize", doResize);
    return () => window.removeEventListener("resize", doResize);
  }, [send, status]);

  useEffect(() => {
    if (status !== "open") return;
    const t = setTimeout(() => {
      const term = termRef.current;
      if (term) send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    }, 60);
    return () => clearTimeout(t);
  }, [status, send]);

  const runCmd = (cmd: string) => {
    if (status !== "open") return;
    send(cmd + "\n");
    termRef.current?.focus();
  };

  const clear = () => termRef.current?.clear();

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1500 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14, gap: 14, flexWrap: "wrap" }}>
          <div>
            <h1 className="as-h1">Terminal</h1>
            <div className="as-h1-sub">
              Interactive bash as <span className="mono">arclap</span> · sudo blocked · pick a ready command on the right or just type
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button onClick={clear}>Clear screen</Button>
            <Button onClick={() => runCmd("clear")}>Clear remote</Button>
          </div>
        </div>

        <div
          className="as-grid-2"
          style={{ alignItems: "start", gap: 14, gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)" }}
        >
          {/* Terminal */}
          <div className="as-card" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{
              padding: "8px 14px",
              background: "#06090d",
              borderBottom: "1px solid var(--as-line)",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}>
              <div className="mono" style={{ fontSize: 11, color: "var(--as-ink-3)", flex: 1 }}>
                arclap@station:~ — xterm.js
              </div>
              <span className="mono" style={{ fontSize: 10, color: "var(--as-ink-4)" }}>
                {status === "open" ? "● connected" : status === "connecting" ? "○ connecting" : "○ offline"}
              </span>
            </div>
            <div
              ref={containerRef}
              style={{ height: 620, background: "#06090d", padding: "8px 4px 4px 12px" }}
              onClick={() => termRef.current?.focus()}
            />
          </div>

          {/* Command palette */}
          <div className="as-card" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column", maxHeight: 668 }}>
            {/* Tab strip */}
            <div style={{
              display: "flex",
              borderBottom: "1px solid var(--as-line)",
              background: "var(--as-bg-2)",
              overflowX: "auto",
              flexShrink: 0,
            }}>
              <Tab id="all" active={tab === "all"} onClick={() => setTab("all")} label="All" />
              {CATEGORIES.map((c) => (
                <Tab
                  key={c.id}
                  id={c.id}
                  active={tab === c.id}
                  onClick={() => setTab(c.id)}
                  label={`${c.icon} ${c.label}`}
                />
              ))}
            </div>

            {/* Search */}
            <div style={{ padding: "10px 12px 6px", borderBottom: "1px solid var(--as-line)", flexShrink: 0 }}>
              <input
                className="as-input mono"
                placeholder="Filter commands… (e.g. iso, journal, df, audit)"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{ width: "100%", fontSize: 12 }}
              />
              <div style={{ marginTop: 6, fontSize: 11, color: "var(--as-ink-3)" }}>
                {visible.length} command{visible.length === 1 ? "" : "s"}
                {search.trim() ? ` · matching "${search.trim()}"` : tab === "all" ? "" : ` · in ${tab}`}
                {" · click to run"}
              </div>
            </div>

            {/* Command list */}
            <div style={{ overflowY: "auto", flex: 1, padding: 10 }}>
              {grouped
                ? Object.entries(grouped).map(([catId, cmds]) => (
                    <div key={catId} style={{ marginBottom: 10 }}>
                      <div style={{
                        fontSize: 10,
                        textTransform: "uppercase",
                        letterSpacing: 0.08,
                        color: "var(--as-ink-3)",
                        fontWeight: 700,
                        marginBottom: 4,
                      }}>
                        {CATEGORIES.find((c) => c.id === catId)?.label ?? catId}
                      </div>
                      {cmds.map((c) => <CmdRow key={c.cmd} c={c} onRun={runCmd} disabled={status !== "open"} />)}
                    </div>
                  ))
                : visible.map((c) => (
                    <CmdRow key={c.cmd} c={c} onRun={runCmd} disabled={status !== "open"} />
                  ))}
              {visible.length === 0 && (
                <div style={{ padding: 18, textAlign: "center", color: "var(--as-ink-3)", fontSize: 12 }}>
                  No commands match. Try clearing the search.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Tab({ id, label, active, onClick }: { id: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={id}
      style={{
        padding: "8px 12px",
        background: active ? "var(--as-fill-1)" : "transparent",
        border: "none",
        borderBottom: active ? "2px solid var(--as-accent)" : "2px solid transparent",
        color: active ? "var(--as-ink-1)" : "var(--as-ink-3)",
        fontSize: 11.5,
        fontWeight: active ? 700 : 500,
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}

function CmdRow({ c, onRun, disabled }: { c: Cmd; onRun: (cmd: string) => void; disabled: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      style={{
        padding: "7px 10px",
        marginBottom: 4,
        border: "1px solid var(--as-line)",
        borderRadius: 5,
        background: "var(--as-fill-1)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        position: "relative",
      }}
      onClick={() => !disabled && onRun(c.cmd)}
    >
      <div className="mono" style={{ fontSize: 11.5, color: "var(--as-ink-1)", marginBottom: 2, wordBreak: "break-word" }}>
        {c.cmd}
      </div>
      <div style={{ fontSize: 10.5, color: "var(--as-ink-3)" }}>{c.desc}</div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          navigator.clipboard.writeText(c.cmd).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          });
        }}
        style={{
          position: "absolute",
          right: 6,
          top: 6,
          padding: "2px 6px",
          background: copied ? "var(--as-accent)" : "transparent",
          color: copied ? "#04140e" : "var(--as-ink-3)",
          border: "1px solid var(--as-line)",
          borderRadius: 3,
          fontSize: 10,
          cursor: "pointer",
        }}
        title="Copy command"
      >
        {copied ? "✓" : "copy"}
      </button>
    </div>
  );
}
