import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Pill } from "../../../components/Pill";
import { TextInput } from "../../../components/FormField";
import { apiFetch } from "../../../lib/api";
import { settings } from "../../../lib/bridge/settings";

interface WifiNetwork {
  ssid: string;
  signal: number;
  security: string;
  band: string;
  in_use: boolean;
}

export function Network() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings.network"],
    queryFn: settings.network,
    refetchInterval: 10_000,
  });
  const { data: scan, refetch: rescan, isFetching: scanning } = useQuery({
    queryKey: ["settings.wifi-scan"],
    queryFn: async () => apiFetch<{ ok: boolean; networks: WifiNetwork[] }>("/settings/network/wifi-scan"),
    enabled: false,
  });
  const [selectedSsid, setSelectedSsid] = useState<string | null>(null);
  const [psk, setPsk] = useState("");
  const [error, setError] = useState<string | null>(null);

  const connect = useMutation({
    mutationFn: async ({ ssid, psk }: { ssid: string; psk: string }) =>
      apiFetch<{ ok: boolean }>("/settings/network/wifi-connect", {
        method: "POST",
        body: { ssid, psk: psk || null },
      }),
    onSuccess: () => {
      setSelectedSsid(null);
      setPsk("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["settings.network"] });
      rescan();
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  const forget = useMutation({
    mutationFn: async (ssid: string) =>
      apiFetch<{ ok: boolean }>("/settings/network/wifi-forget", {
        method: "POST",
        body: { ssid },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings.network"] });
      rescan();
    },
  });

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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Wi-Fi</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <Pill tone={wifi.connected ? "ok" : "gray"}>{wifi.ssid || "not connected"}</Pill>
            <Button onClick={() => rescan()} disabled={scanning}>
              {scanning ? "Scanning…" : "Scan"}
            </Button>
          </div>
        </div>
        <Row label="SSID" val={wifi.ssid || "—"} mono />
        <Row label="Security" val={wifi.security} />
        <Row label="Band" val={wifi.band} />
        <Row label="Signal" val={wifi.signal_dbm !== null ? `${wifi.signal_dbm} dBm` : "—"} mono />
        {wifi.connected && (
          <div style={{ marginTop: 10 }}>
            <Button onClick={() => forget.mutate(wifi.ssid)} disabled={forget.isPending}>
              Forget this network
            </Button>
          </div>
        )}
        {scan?.networks && scan.networks.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--as-ink-3)", marginBottom: 8 }}>
              Available networks
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto" }}>
              {scan.networks.map((n) => (
                <div
                  key={n.ssid}
                  onClick={() => setSelectedSsid(n.ssid)}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "6px 10px",
                    background: selectedSsid === n.ssid ? "var(--as-fill-2)" : "transparent",
                    borderRadius: 4,
                    cursor: "pointer",
                    border: "1px solid var(--as-line)",
                  }}
                >
                  <span style={{ fontSize: 12, fontFamily: "var(--as-mono)" }}>
                    {n.ssid} {n.in_use && "★"}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--as-ink-3)" }}>
                    {n.band} · {n.signal}% · {n.security || "open"}
                  </span>
                </div>
              ))}
            </div>
            {selectedSsid && (
              <div style={{ marginTop: 10, display: "flex", gap: 6, alignItems: "center" }}>
                <TextInput
                  type="password"
                  placeholder="WPA passphrase (leave blank for open)"
                  value={psk}
                  onChange={(e) => setPsk(e.target.value)}
                  style={{ flex: 1 }}
                />
                <Button
                  onClick={() => connect.mutate({ ssid: selectedSsid, psk })}
                  disabled={connect.isPending}
                >
                  {connect.isPending ? "Connecting…" : "Connect"}
                </Button>
              </div>
            )}
            {error && (
              <div style={{ marginTop: 8, fontSize: 12, color: "var(--as-bad)" }}>{error}</div>
            )}
          </div>
        )}
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
