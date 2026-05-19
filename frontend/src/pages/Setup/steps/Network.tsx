import { useQuery } from "@tanstack/react-query";
import { Icon, I } from "../../../components/icons";
import { Pill } from "../../../components/Pill";
import { setup } from "../../../lib/bridge/setup";

export function Network() {
  const { data, isLoading } = useQuery({
    queryKey: ["setup.network"],
    queryFn: setup.network,
    refetchInterval: 3000,
  });

  if (isLoading || !data) {
    return <div style={{ textAlign: "center", color: "var(--as-ink-3)" }}>Probing interfaces…</div>;
  }

  const rows: Array<[string, string, string]> = [
    ["Ethernet", "Wired link", data.eth],
    ["Wi-Fi", "Wireless link", data.wifi],
    ["Cellular", "LTE failover", data.cell],
    ["NTP", "Time sync", data.ntp === "ok" ? "up" : data.ntp],
  ];

  return (
    <div>
      {rows.map(([name, detail, status], i) => {
        const up = status === "up";
        return (
          <div
            key={name}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 0",
              borderBottom: i < rows.length - 1 ? "1px solid var(--as-line)" : "none",
            }}
          >
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: up ? "var(--as-accent-soft)" : "var(--as-surface-2)",
                color: up ? "var(--as-accent-2)" : "var(--as-ink-3)",
                display: "grid",
                placeItems: "center",
              }}
            >
              {up ? <Icon d={I.check} size={16} stroke={2.5} /> : <Icon d={I.clock} size={14} />}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{name}</div>
              <div style={{ fontSize: 11.5, color: "var(--as-ink-3)", marginTop: 1 }}>{detail}</div>
            </div>
            <Pill tone={up ? "ok" : "gray"}>{status}</Pill>
          </div>
        );
      })}
    </div>
  );
}
