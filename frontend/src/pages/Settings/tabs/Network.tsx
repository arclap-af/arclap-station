import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Pill } from "../../../components/Pill";
import { TextInput } from "../../../components/FormField";
import { apiFetch } from "../../../lib/api";
import { settings } from "../../../lib/bridge/settings";

// v0.9 Network tab — everything editable from the cockpit:
// - Ethernet IP (DHCP ↔ static)
// - WiFi scan / connect / forget (v0.7)
// - Hostname
// - System DNS resolvers
// - Custom NTP servers
//
// Backend endpoints: /settings/network/{ethernet,wifi-*,hostname,dns,ntp,connections}

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

  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <EthernetCard data={data.ethernet} />
      <WifiCard
        wifi={data.wifi}
        scan={scan?.networks ?? []}
        scanning={scanning}
        onRescan={() => rescan()}
        onChanged={() => qc.invalidateQueries({ queryKey: ["settings.network"] })}
      />
      <HostnameCard />
      <DnsCard />
      <NtpCard />
      <CellularCard cell={data.cellular} />
      <ProbesCard probes={data.probes} />
    </div>
  );
}

// ----- Ethernet ----------------------------------------------------------

interface EthernetData {
  connected: boolean;
  interface: string;
  mode: string;
  ipv4: string;
  gateway: string;
  dns: string;
  mac: string;
}

function EthernetCard({ data }: { data: EthernetData }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"dhcp" | "static">(data.mode.toLowerCase().includes("dhcp") ? "dhcp" : "static");
  const [address, setAddress] = useState(data.ipv4 !== "—" ? `${data.ipv4}/24` : "");
  const [gateway, setGateway] = useState(data.gateway !== "—" ? data.gateway : "");
  const [dns, setDns] = useState(data.dns !== "—" ? data.dns : "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () =>
      apiFetch<{ ok: boolean }>("/settings/network/ethernet", {
        method: "POST",
        body: { interface: data.interface, mode, address, gateway, dns },
      }),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["settings.network"] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="as-card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>Ethernet</div>
        <Pill tone={data.connected ? "ok" : "gray"}>{data.connected ? "Connected" : "Down"}</Pill>
      </div>
      <Row label="Interface" val={`${data.interface} · ${data.mode}`} mono />
      <Row label="IPv4" val={data.ipv4} mono />
      <Row label="Gateway" val={data.gateway} mono />
      <Row label="DNS" val={data.dns} mono />
      <Row label="MAC" val={data.mac} mono />
      {!editing && (
        <div style={{ marginTop: 10 }}>
          <Button onClick={() => setEditing(true)}>Edit IP config</Button>
        </div>
      )}
      {editing && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--as-line)" }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <label style={{ display: "flex", gap: 4, alignItems: "center", fontSize: 13 }}>
              <input type="radio" checked={mode === "dhcp"} onChange={() => setMode("dhcp")} /> DHCP
            </label>
            <label style={{ display: "flex", gap: 4, alignItems: "center", fontSize: 13 }}>
              <input type="radio" checked={mode === "static"} onChange={() => setMode("static")} /> Static IP
            </label>
          </div>
          {mode === "static" && (
            <>
              <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginBottom: 4 }}>Address (CIDR)</div>
              <TextInput className="mono" placeholder="192.168.10.50/24" value={address} onChange={(e) => setAddress(e.target.value)} style={{ marginBottom: 8, width: "100%" }} />
              <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginBottom: 4 }}>Gateway</div>
              <TextInput className="mono" placeholder="192.168.10.1" value={gateway} onChange={(e) => setGateway(e.target.value)} style={{ marginBottom: 8, width: "100%" }} />
              <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginBottom: 4 }}>DNS (comma-separated, optional)</div>
              <TextInput className="mono" placeholder="1.1.1.1, 9.9.9.9" value={dns} onChange={(e) => setDns(e.target.value)} style={{ marginBottom: 8, width: "100%" }} />
            </>
          )}
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "Applying…" : "Apply"}
            </Button>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
          </div>
          {mode === "static" && (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--as-warn)" }}>
              ⚠ Applying a wrong static IP can disconnect you from the cockpit. Double-check before clicking Apply.
            </div>
          )}
          {error && <div style={{ marginTop: 6, fontSize: 12, color: "var(--as-bad)" }}>{error}</div>}
        </div>
      )}
    </div>
  );
}

// ----- WiFi --------------------------------------------------------------

interface WifiData {
  connected: boolean;
  ssid: string;
  security: string;
  band: string;
  signal_dbm: number | null;
}

function WifiCard({
  wifi, scan, scanning, onRescan, onChanged,
}: { wifi: WifiData; scan: WifiNetwork[]; scanning: boolean; onRescan: () => void; onChanged: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [psk, setPsk] = useState("");
  const [error, setError] = useState<string | null>(null);

  const connect = useMutation({
    mutationFn: async () =>
      apiFetch<{ ok: boolean }>("/settings/network/wifi-connect", {
        method: "POST",
        body: { ssid: selected, psk: psk || null },
      }),
    onSuccess: () => { setSelected(null); setPsk(""); setError(null); onChanged(); onRescan(); },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });
  const forget = useMutation({
    mutationFn: async (ssid: string) =>
      apiFetch<{ ok: boolean }>("/settings/network/wifi-forget", {
        method: "POST",
        body: { ssid },
      }),
    onSuccess: () => { onChanged(); onRescan(); },
  });

  return (
    <div className="as-card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>Wi-Fi</div>
        <div style={{ display: "flex", gap: 6 }}>
          <Pill tone={wifi.connected ? "ok" : "gray"}>{wifi.ssid || "not connected"}</Pill>
          <Button onClick={onRescan} disabled={scanning}>{scanning ? "Scanning…" : "Scan"}</Button>
        </div>
      </div>
      <Row label="SSID" val={wifi.ssid || "—"} mono />
      <Row label="Security" val={wifi.security} />
      <Row label="Band" val={wifi.band} />
      <Row label="Signal" val={wifi.signal_dbm !== null ? `${wifi.signal_dbm} dBm` : "—"} mono />
      {wifi.connected && (
        <div style={{ marginTop: 10 }}>
          <Button onClick={() => forget.mutate(wifi.ssid)} disabled={forget.isPending}>Forget this network</Button>
        </div>
      )}
      {scan.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--as-ink-3)", marginBottom: 6 }}>Available networks</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto" }}>
            {scan.map((n) => (
              <div
                key={n.ssid}
                onClick={() => setSelected(n.ssid)}
                style={{
                  padding: "6px 10px",
                  background: selected === n.ssid ? "var(--as-fill-2)" : "transparent",
                  border: "1px solid var(--as-line)",
                  borderRadius: 4,
                  display: "flex",
                  justifyContent: "space-between",
                  cursor: "pointer",
                }}
              >
                <span style={{ fontSize: 12, fontFamily: "var(--as-mono)" }}>{n.ssid} {n.in_use && "★"}</span>
                <span style={{ fontSize: 11, color: "var(--as-ink-3)" }}>{n.band} · {n.signal}% · {n.security || "open"}</span>
              </div>
            ))}
          </div>
          {selected && (
            <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
              <TextInput type="password" placeholder="WPA passphrase" value={psk} onChange={(e) => setPsk(e.target.value)} style={{ flex: 1 }} />
              <Button onClick={() => connect.mutate()} disabled={connect.isPending}>{connect.isPending ? "Connecting…" : "Connect"}</Button>
            </div>
          )}
          {error && <div style={{ marginTop: 8, fontSize: 12, color: "var(--as-bad)" }}>{error}</div>}
        </div>
      )}
    </div>
  );
}

// ----- Hostname ----------------------------------------------------------

function HostnameCard() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings.network.hostname"],
    queryFn: async () => apiFetch<{ hostname: string }>("/settings/general"),
  });
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () =>
      apiFetch<{ ok: boolean; note: string }>("/settings/network/hostname", {
        method: "POST",
        body: { hostname: draft.trim() },
      }),
    onSuccess: (r) => {
      setStatus(r.note);
      qc.invalidateQueries({ queryKey: ["settings.general"] });
      qc.invalidateQueries({ queryKey: ["settings.network.hostname"] });
    },
    onError: (e) => setStatus(`Error: ${e instanceof Error ? e.message : String(e)}`),
  });

  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Hostname</div>
      <Row label="Current" val={data?.hostname || "—"} mono />
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginBottom: 4 }}>
          New hostname (letters / digits / hyphens, 1–63 chars)
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <TextInput
            className="mono"
            placeholder="arclap-st-newname"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            style={{ flex: 1 }}
          />
          <Button variant="primary" onClick={() => save.mutate()} disabled={!draft || save.isPending}>
            {save.isPending ? "Renaming…" : "Rename"}
          </Button>
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--as-warn)" }}>
          ⚠ The cockpit URL changes to <code>https://&lt;new-name&gt;.local/</code>. The current
          tab will lose connection after rename — bookmark the new URL first.
        </div>
        {status && <div style={{ marginTop: 6, fontSize: 12, color: "var(--as-ink-3)" }}>{status}</div>}
      </div>
    </div>
  );
}

// ----- DNS ---------------------------------------------------------------

function DnsCard() {
  const [servers, setServers] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () =>
      apiFetch<{ ok: boolean; servers: string[] }>("/settings/network/dns", {
        method: "POST",
        body: { servers },
      }),
    onSuccess: (r) => setStatus(r.servers.length ? `Applied: ${r.servers.join(", ")}` : "Cleared (back to DHCP / fallback)"),
    onError: (e) => setStatus(`Error: ${e instanceof Error ? e.message : String(e)}`),
  });

  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>DNS resolvers (system-wide)</div>
      <div style={{ fontSize: 12, color: "var(--as-ink-3)", marginBottom: 8 }}>
        Comma-separated IPs. Empty = clear override and use DHCP-pushed + fallback resolvers
        (1.1.1.1 / 9.9.9.9 / 8.8.8.8 with DNS-over-TLS opportunistic).
      </div>
      <TextInput
        className="mono"
        placeholder="1.1.1.1, 9.9.9.9, 2606:4700:4700::1111"
        value={servers}
        onChange={(e) => setServers(e.target.value)}
        style={{ width: "100%", marginBottom: 8 }}
      />
      <div style={{ display: "flex", gap: 6 }}>
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Applying…" : "Apply"}
        </Button>
        <Button onClick={() => { setServers(""); save.mutate(); }} disabled={save.isPending}>
          Clear override
        </Button>
      </div>
      {status && <div style={{ marginTop: 8, fontSize: 12, color: "var(--as-ink-3)" }}>{status}</div>}
    </div>
  );
}

// ----- NTP ---------------------------------------------------------------

function NtpCard() {
  const [servers, setServers] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () =>
      apiFetch<{ ok: boolean; servers: string[] }>("/settings/network/ntp", {
        method: "POST",
        body: { servers },
      }),
    onSuccess: (r) => setStatus(r.servers.length ? `Applied: ${r.servers.join(", ")}` : "Cleared (back to default fallback)"),
    onError: (e) => setStatus(`Error: ${e instanceof Error ? e.message : String(e)}`),
  });

  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>NTP time servers</div>
      <div style={{ fontSize: 12, color: "var(--as-ink-3)", marginBottom: 8 }}>
        Comma-separated host names or IPs. Empty = use the default ladder
        (time.cloudflare.com → time.google.com → pool.ntp.org).
      </div>
      <TextInput
        className="mono"
        placeholder="time.cloudflare.com, 0.pool.ntp.org"
        value={servers}
        onChange={(e) => setServers(e.target.value)}
        style={{ width: "100%", marginBottom: 8 }}
      />
      <div style={{ display: "flex", gap: 6 }}>
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Applying…" : "Apply"}
        </Button>
        <Button onClick={() => { setServers(""); save.mutate(); }} disabled={save.isPending}>
          Use defaults
        </Button>
      </div>
      {status && <div style={{ marginTop: 8, fontSize: 12, color: "var(--as-ink-3)" }}>{status}</div>}
    </div>
  );
}

// ----- Cellular (read-only placeholder, unchanged) -----------------------

function CellularCard({ cell }: { cell: { status: string; modem: string; carrier: string; signal_dbm: number | null; apn: string; data_mb: number } }) {
  return (
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
  );
}

// ----- Probes ------------------------------------------------------------

function ProbesCard({ probes }: { probes: Array<{ label: string; result: string; level: "ok" | "warn" | "bad" }> }) {
  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Connectivity probes</div>
      {probes.map((p, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: i < probes.length - 1 ? "1px solid var(--as-line)" : "none" }}>
          <div style={{ fontSize: 13, fontFamily: "var(--as-mono)" }}>{p.label}</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--as-ink-3)", fontFamily: "var(--as-mono)" }}>{p.result}</span>
            <Pill tone={p.level}>{p.level}</Pill>
          </div>
        </div>
      ))}
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
