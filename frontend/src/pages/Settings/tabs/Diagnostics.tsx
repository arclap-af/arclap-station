import { useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Pill } from "../../../components/Pill";
import { apiFetch } from "../../../lib/api";

// v0.8 Diagnostics tab — surfaces everything the backend `/api/diag/*`
// endpoints now expose. This is the "all the things shipped overnight"
// page so operators can actually see service health, boot history,
// SMART, latency percentiles, and grab a support bundle in one click.

interface ServiceRow { unit: string; active: string; enabled: string; ok: boolean }
interface BootRow { index: number; id: string; started_at: string; ended_at: string | null; reason: string }
interface SmartDevice { device: string; model?: string; serial?: string; passed?: boolean; attributes?: Array<{ name: string; value: number; thresh?: number; worst?: number }>; error?: string }
interface PercentileRow { count: number; p50_s: number; p95_s: number; avg_s: number }
interface TunnelInfo { installed: boolean; configured: boolean; up: boolean; address: string | null; peer: { pubkey: string; endpoint: string | null; latest_handshake_age_sec: number | null; rx_bytes: number; tx_bytes: number } | null }

export function Diagnostics() {
  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <ServicesCard />
      <SmartCard />
      <TunnelCard />
      <BootHistoryCard />
      <PercentilesCard />
      <SupportBundleCard />
    </div>
  );
}

function ServicesCard() {
  const { data } = useQuery({
    queryKey: ["diag.services"],
    queryFn: () => apiFetch<{ services: ServiceRow[] }>("/diag/services"),
    refetchInterval: 30_000,
  });
  return (
    <div className="as-card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>Service health</div>
        <Pill tone={data?.services?.every((s) => s.ok) ? "ok" : "warn"}>
          {data ? `${data.services.filter((s) => s.ok).length}/${data.services.length} active` : "loading"}
        </Pill>
      </div>
      {(data?.services ?? []).map((s) => (
        <div
          key={s.unit}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "6px 0",
            borderBottom: "1px solid var(--as-line)",
          }}
        >
          <span style={{ fontSize: 12, fontFamily: "var(--as-mono)" }}>{s.unit}</span>
          <span style={{ display: "flex", gap: 8 }}>
            <Pill tone={s.ok ? "ok" : "bad"}>{s.active}</Pill>
            <span style={{ fontSize: 11, color: "var(--as-ink-3)" }}>{s.enabled}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function SmartCard() {
  const { data } = useQuery({
    queryKey: ["diag.smart"],
    queryFn: () => apiFetch<{ devices: SmartDevice[] }>("/diag/smart"),
    refetchInterval: 60_000,
  });
  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Storage SMART</div>
      {(data?.devices ?? []).map((d) => (
        <div key={d.device} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ fontSize: 12, fontFamily: "var(--as-mono)" }}>{d.device}</span>
            {d.passed !== undefined ? (
              <Pill tone={d.passed ? "ok" : "bad"}>{d.passed ? "passed" : "FAILED"}</Pill>
            ) : (
              <Pill tone="gray">unknown</Pill>
            )}
          </div>
          {d.model && <div style={{ fontSize: 11, color: "var(--as-ink-3)" }}>{d.model}</div>}
          {d.error && (
            <div style={{ fontSize: 11, color: "var(--as-warn)", marginTop: 4 }}>{d.error}</div>
          )}
          {d.attributes?.map((a) => (
            <div key={a.name} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontFamily: "var(--as-mono)" }}>
              <span style={{ color: "var(--as-ink-3)" }}>{a.name}</span>
              <span>{a.value}</span>
            </div>
          ))}
        </div>
      ))}
      {data && data.devices.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>No storage devices probed.</div>
      )}
    </div>
  );
}

function TunnelCard() {
  const { data, refetch } = useQuery({
    queryKey: ["diag.tunnel"],
    queryFn: () => apiFetch<TunnelInfo>("/diag/tunnel"),
    refetchInterval: 15_000,
  });
  return (
    <div className="as-card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>Support tunnel (WireGuard)</div>
        <Pill tone={data?.up ? "ok" : data?.installed ? "gray" : "warn"}>
          {!data?.installed ? "not installed" : data.up ? "up" : "down"}
        </Pill>
      </div>
      <Row label="WireGuard" val={data?.installed ? "installed" : "not installed"} />
      <Row label="Config" val={data?.configured ? "provisioned" : "missing"} />
      {data?.up && (
        <>
          <Row label="Tunnel IP" val={data.address ?? "—"} mono />
          {data.peer && (
            <>
              <Row label="Peer endpoint" val={data.peer.endpoint ?? "—"} mono />
              <Row
                label="Last handshake"
                val={
                  data.peer.latest_handshake_age_sec !== null
                    ? `${data.peer.latest_handshake_age_sec}s ago`
                    : "never"
                }
              />
              <Row label="Rx / Tx" val={`${(data.peer.rx_bytes / 1024).toFixed(0)} KB / ${(data.peer.tx_bytes / 1024).toFixed(0)} KB`} mono />
            </>
          )}
        </>
      )}
      {data?.installed && (
        <div style={{ marginTop: 12, display: "flex", gap: 6 }}>
          <Button
            onClick={async () => {
              await apiFetch("/diag/tunnel/up", { method: "POST" });
              refetch();
            }}
            disabled={!data.configured || data.up}
          >
            Open tunnel
          </Button>
          <Button
            onClick={async () => {
              await apiFetch("/diag/tunnel/down", { method: "POST" });
              refetch();
            }}
            disabled={!data.up}
          >
            Close tunnel
          </Button>
        </div>
      )}
    </div>
  );
}

function BootHistoryCard() {
  const { data } = useQuery({
    queryKey: ["diag.boot-history"],
    queryFn: () => apiFetch<{ boots: BootRow[] }>("/diag/boot-history?limit=10"),
    refetchInterval: 0,
  });
  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Boot history (last 10)</div>
      {(data?.boots ?? []).length === 0 && (
        <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>
          {data ? "No boot records available." : "Loading…"}
        </div>
      )}
      {(data?.boots ?? []).map((b) => (
        <div key={b.id} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid var(--as-line)" }}>
          <span style={{ fontSize: 11, fontFamily: "var(--as-mono)" }}>
            {b.started_at}
          </span>
          <Pill tone={b.reason === "clean_boot" ? "ok" : b.reason === "kernel_panic" ? "bad" : "warn"}>
            {b.reason}
          </Pill>
        </div>
      ))}
    </div>
  );
}

function PercentilesCard() {
  const { data } = useQuery({
    queryKey: ["diag.percentiles"],
    queryFn: () => apiFetch<{ endpoints: Record<string, PercentileRow> }>("/diag/percentiles"),
    refetchInterval: 15_000,
  });
  const sorted = Object.entries(data?.endpoints ?? {}).sort((a, b) => b[1].p95_s - a[1].p95_s);
  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>
        Endpoint p50 / p95 (ms)
      </div>
      <div style={{ maxHeight: 280, overflowY: "auto" }}>
        {sorted.map(([endpoint, p]) => (
          <div key={endpoint} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--as-line)" }}>
            <span style={{ fontSize: 11, fontFamily: "var(--as-mono)", maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {endpoint}
            </span>
            <span style={{ fontSize: 11, fontFamily: "var(--as-mono)", color: "var(--as-ink-3)" }}>
              n={p.count} · {(p.p50_s * 1000).toFixed(0)} / {(p.p95_s * 1000).toFixed(0)} ms
            </span>
          </div>
        ))}
        {sorted.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>
            No traffic yet. Browse a few pages and refresh.
          </div>
        )}
      </div>
    </div>
  );
}

function SupportBundleCard() {
  const dl = () => {
    // Trigger a browser download via auth-cookied GET.
    window.location.href = "/api/diag/support-bundle";
  };
  const auditExport = () => {
    window.location.href = "/api/settings/audit/export";
  };
  return (
    <div className="as-card">
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Operations bundles</div>
      <div style={{ fontSize: 12.5, color: "var(--as-ink-3)", marginBottom: 14 }}>
        Self-contained, redacted exports for support tickets and legal forensics.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Button onClick={dl}>
          Download support bundle (.tar.gz)
        </Button>
        <Button onClick={auditExport}>
          Download signed audit export (.json)
        </Button>
      </div>
      <div style={{ marginTop: 14, fontSize: 11, color: "var(--as-ink-3)", lineHeight: 1.5 }}>
        Support bundle contains: logs (last 1000 lines) · journalctl ·
        dmesg · gzipped DB dump · audit tail · station config (PIN
        token redacted). No photos, no destination secrets.
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
