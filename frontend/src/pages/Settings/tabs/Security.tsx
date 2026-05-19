import { useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { settings } from "../../../lib/bridge/settings";

export function Security() {
  const { data } = useQuery({ queryKey: ["settings.security"], queryFn: settings.security });
  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Access</div>
        <Row label="UI PIN" val={`Set · last changed ${data.pin_changed_days_ago}d ago`} />
        <Row label="Auto-lock" val={`${data.auto_lock_minutes} minutes`} mono />
        <Button style={{ width: "100%", marginTop: 14 }}>Change PIN</Button>
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>TLS / HTTPS</div>
        <Row label="Cert type" val={data.tls.type} />
        <Row label="Fingerprint" val={data.tls.fingerprint} mono />
        <Row label="Expires" val={data.tls.expires} mono />
        <Row label="HSTS" val={data.tls.hsts ? "enabled" : "disabled"} />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>SSH</div>
        <Row label="Status" val={data.ssh.enabled ? `Key-only · port ${data.ssh.port}` : "Disabled"} />
        <Row label="Authorized keys" val={String(data.ssh.key_count)} mono />
        <Row label="Last login" val={data.ssh.last_login ?? "—"} />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>API tokens</div>
        {data.tokens.map((t) => (
          <Row key={t.name} label={t.name} val={t.prefix} mono />
        ))}
        <Button style={{ width: "100%", marginTop: 12 }}>+ Generate token</Button>
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
