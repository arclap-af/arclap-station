interface URLBarProps {
  ip: string;
  hostname: string;
  status: "online" | "warn" | "offline";
}

export function URLBar({ ip, hostname, status }: URLBarProps) {
  const statusColor =
    status === "online" ? "var(--as-accent-2)" : status === "warn" ? "var(--as-warn)" : "var(--as-bad)";
  const statusLabel = status === "online" ? "online" : status === "warn" ? "warn" : "offline";
  return (
    <div className="as-urlbar">
      <span className="url">
        {/* Derive from window.location so the cockpit's actual scheme
            (https with the Caddy self-signed cert) shows correctly; also
            falls back to hostname when ip is unknown ('—'). */}
        {typeof window !== "undefined" ? window.location.protocol : "https:"}//
        {ip && ip !== "—" ? ip : hostname || (typeof window !== "undefined" ? window.location.host : "")}/
      </span>
      <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--as-ink-4)" }}>
        {hostname} · <span style={{ color: statusColor }}>● {statusLabel}</span>
      </span>
    </div>
  );
}
