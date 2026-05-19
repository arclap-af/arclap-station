import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { auth } from "../../../lib/bridge/auth";
import { settings } from "../../../lib/bridge/settings";

export function Security() {
  const { data } = useQuery({ queryKey: ["settings.security"], queryFn: settings.security });
  const [pinModalOpen, setPinModalOpen] = useState(false);
  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Access</div>
        <Row label="UI PIN" val={`Set · last changed ${data.pin_changed_days_ago}d ago`} />
        <Row label="Auto-lock" val={`${data.auto_lock_minutes} minutes`} mono />
        <Button style={{ width: "100%", marginTop: 14 }} onClick={() => setPinModalOpen(true)}>
          Change PIN
        </Button>
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
        {data.tokens.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--as-ink-3)" }}>No tokens issued.</div>
        ) : (
          data.tokens.map((t) => <Row key={t.name} label={t.name} val={t.prefix} mono />)
        )}
      </div>
      {pinModalOpen && <ChangePinModal onClose={() => setPinModalOpen(false)} />}
    </div>
  );
}

function ChangePinModal({ onClose }: { onClose: () => void }) {
  const [currentPin, setCurrentPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirmPin, setConfirmPin] = useState("");
  const [error, setError] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () => auth.changePin(currentPin, newPin),
    onSuccess: () => {
      onClose();
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!/^\d{4,12}$/.test(currentPin)) {
      setError("Current PIN must be 4–12 digits.");
      return;
    }
    if (!/^\d{4,12}$/.test(newPin)) {
      setError("New PIN must be 4–12 digits.");
      return;
    }
    if (newPin !== confirmPin) {
      setError("New PIN and confirmation do not match.");
      return;
    }
    if (newPin === currentPin) {
      setError("New PIN must differ from the current one.");
      return;
    }
    change.mutate();
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "grid",
        placeItems: "center",
        zIndex: 100,
        padding: 20,
      }}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        className="as-card"
        style={{ width: "100%", maxWidth: 380, padding: 22 }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>Change PIN</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ fontSize: 11, color: "var(--as-ink-3)" }}>
            Current PIN
            <input
              type="password"
              inputMode="numeric"
              autoComplete="current-password"
              className="as-input mono"
              maxLength={12}
              value={currentPin}
              onChange={(e) => setCurrentPin(e.target.value.replace(/\D/g, ""))}
              style={{ width: "100%", marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 11, color: "var(--as-ink-3)" }}>
            New PIN (4–12 digits)
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              className="as-input mono"
              maxLength={12}
              value={newPin}
              onChange={(e) => setNewPin(e.target.value.replace(/\D/g, ""))}
              style={{ width: "100%", marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 11, color: "var(--as-ink-3)" }}>
            Confirm new PIN
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              className="as-input mono"
              maxLength={12}
              value={confirmPin}
              onChange={(e) => setConfirmPin(e.target.value.replace(/\D/g, ""))}
              style={{ width: "100%", marginTop: 4 }}
            />
          </label>
        </div>
        {error && (
          <div
            className="as-banner bad"
            role="alert"
            style={{ marginTop: 12, padding: "8px 10px", fontSize: 12 }}
          >
            {error}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <Button type="button" onClick={onClose} disabled={change.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={change.isPending}>
            {change.isPending ? "Saving…" : "Change PIN"}
          </Button>
        </div>
      </form>
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
