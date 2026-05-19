import { useQuery } from "@tanstack/react-query";

import { Pill } from "../../../components/Pill";
import { settings } from "../../../lib/bridge/settings";

export function Network() {
  const { data } = useQuery({ queryKey: ["settings.network"], queryFn: settings.network });
  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;
  const eth = data.ethernet;
  const wifi = data.wifi;
  const cell = data.cellular;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Ethernet</div>
          <Pill tone={eth.connected ? "ok" : "gray"}>{eth.connected ? "Connected" : "Down"}</Pill>
        </div>
        <Row label="Interface" val={`${eth.interface} · ${eth.mode}`} mono />
        <Row label="IPv4" val={eth.ipv4} mono />
        <Row label="Gateway" val={eth.gateway} mono />
        <Row label="DNS" val={eth.dns} mono />
        <Row label="MAC" val={eth.mac} mono />
      </div>
      <div className="as-card">
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Wi-Fi</div>
          <Pill tone={wifi.connected ? "ok" : "gray"}>{wifi.ssid || "not connected"}</Pill>
        </div>
        <Row label="SSID" val={wifi.ssid || "—"} mono />
        <Row label="Security" val={wifi.security} />
        <Row label="Band" val={wifi.band} />
        <Row label="Signal" val={wifi.signal_dbm !== null ? `${wifi.signal_dbm} dBm` : "—"} mono />
      </div>
      <div className="as-card">
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Cellular failover</div>
          <Pill tone={cell.status === "up" ? "ok" : "gray"}>{cell.status}</Pill>
        </div>
        <Row label="Modem" val={cell.modem || "—"} />
        <Row label="Carrier" val={cell.carrier || "—"} />
        <Row label="Signal" val={cell.signal_dbm !== null ? `${cell.signal_dbm} dBm` : "—"} mono />
        <Row label="APN" val={cell.apn || "—"} mono />
        <Row label="Data used" val={`${cell.data_mb.toFixed(1)} MB`} mono />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Connectivity probes</div>
        {data.probes.map((p, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: i < data.probes.length - 1 ? "1px solid var(--as-line)" : "none" }}>
            <div style={{ fontSize: 13, fontFamily: "var(--as-mono)" }}>{p.label}</div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "var(--as-ink-3)", fontFamily: "var(--as-mono)" }}>{p.result}</span>
              <Pill tone={p.level}>{p.level}</Pill>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Row({ label, val, mono }: { label: string; val: string; mono?: boolean }) {
  return (
    <div className="as-stat-row">
      <span className="as-stat-label">{label}</span>
      <span className={`as-stat-val${mono ? " mono" : ""}`}>{val}</span>
    </div>
  );
}
