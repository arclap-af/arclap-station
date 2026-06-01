import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Pill } from "../../../components/Pill";
import { apiFetch } from "../../../lib/api";
import { settings } from "../../../lib/bridge/settings";

interface UpdateCheck {
  current: string;
  latest: string | null;
  update_available: boolean;
  reachable: boolean;
  releases_url: string;
}

export function System() {
  const { data } = useQuery({ queryKey: ["settings.system"], queryFn: settings.system });
  const [modal, setModal] = useState<"reboot" | "factory" | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [update, setUpdate] = useState<UpdateCheck | null>(null);

  const checkUpdate = useMutation({
    mutationFn: () => apiFetch<UpdateCheck>("/system/update/check"),
    onSuccess: (r) => {
      setUpdate(r);
      showToast(
        !r.reachable
          ? "Couldn't reach GitHub — check connectivity"
          : r.update_available
            ? `Update available: ${r.latest}`
            : "You're on the latest version",
      );
    },
    onError: (e) => showToast(e instanceof Error ? e.message : String(e)),
  });

  const restart = useMutation({
    mutationFn: () => settings.restart("arclap-station"),
    onSuccess: () => showToast("Service restart scheduled"),
    onError: (e) => showToast(e instanceof Error ? e.message : String(e)),
  });

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start", position: "relative" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Hardware</div>
        <Row label="Model" val={data.hardware.model} />
        <Row label="Serial" val={data.hardware.serial} mono />
        <Row
          label="CPU"
          val={`${data.hardware.cpu_pct.toFixed(0)}% · ${data.hardware.cpu_temp_c.toFixed(1)}°C`}
          mono
        />
        <Row
          label="Memory"
          val={`${data.hardware.memory_used_mb} / ${data.hardware.memory_total_mb} MB`}
          mono
        />
        <Row label="UPS" val={data.hardware.ups} />
        <Row label="Watchdog" val={data.hardware.watchdog} />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Software</div>
        <Row label="Running" val={update?.current ?? data.firmware.current} mono />
        <Row
          label="Latest on GitHub"
          val=""
          customVal={
            update
              ? update.reachable
                ? <Pill tone={update.update_available ? "warn" : "ok"}>
                    {update.latest ?? "—"}{update.update_available ? " · update" : " · current"}
                  </Pill>
                : <Pill tone="gray">offline</Pill>
              : <span style={{ color: "var(--as-ink-4)", fontSize: 12 }}>not checked</span>
          }
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
          <Button onClick={() => checkUpdate.mutate()} disabled={checkUpdate.isPending}>
            {checkUpdate.isPending ? "Checking…" : "Check for updates"}
          </Button>
          {update?.update_available && (
            <div style={{ fontSize: 11.5, color: "var(--as-ink-3)", lineHeight: 1.55 }}>
              To update, SSH to the Pi and run the documented installer:
              <div className="mono" style={{
                marginTop: 6, padding: "8px 10px", background: "var(--as-bg-2)",
                border: "1px solid var(--as-line)", borderRadius: 6, fontSize: 11,
                wordBreak: "break-all", color: "var(--as-ink-2)",
              }}>
                curl -fsSL https://raw.githubusercontent.com/arclap-af/arclap-station/main/install.sh | sudo bash
              </div>
              <a href={update.releases_url} target="_blank" rel="noreferrer" className="as-link" style={{ marginTop: 6, display: "inline-block" }}>
                View changes on GitHub →
              </a>
            </div>
          )}
        </div>
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Cloud pairing</div>
        <Row
          label="Status"
          val=""
          customVal={
            <Pill tone={data.cloud.paired ? "ok" : "gray"}>
              {data.cloud.paired ? "Paired" : "Standalone"}
            </Pill>
          }
        />
        <Row label="MQTT broker" val={data.cloud.broker ?? "—"} mono />
        <Row label="Cockpit URL" val={data.cloud.cockpit_url ?? "—"} mono />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14, color: "var(--as-bad)" }}>
          Danger zone
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Button onClick={() => restart.mutate()} disabled={restart.isPending}>
            {restart.isPending ? "Restarting…" : "Restart capture service"}
          </Button>
          <Button onClick={() => setModal("reboot")}>Reboot Pi</Button>
          <Button
            style={{ color: "var(--as-bad)" }}
            onClick={() => setModal("factory")}
          >
            Factory reset
          </Button>
        </div>
      </div>
      {modal === "reboot" && (
        <ConfirmModal
          title="Reboot Pi?"
          body="The station will power-cycle. Captures and uploads will pause for ~30 seconds."
          confirmLabel="Reboot"
          danger
          onClose={() => setModal(null)}
          onConfirm={async (pin) => {
            await settings.reboot(pin);
            setModal(null);
            showToast("Reboot scheduled — cockpit will become unreachable shortly");
          }}
        />
      )}
      {modal === "factory" && (
        <ConfirmModal
          title="Factory reset?"
          body="This will wipe destinations, schedules, audit log, and station identity. Captured photos are kept unless you tick the box."
          confirmLabel="Wipe and restart"
          danger
          showPurgeCheckbox
          onClose={() => setModal(null)}
          onConfirm={async (pin, purge) => {
            await settings.factoryReset(pin, purge);
            setModal(null);
            showToast("Factory reset complete; service restarting");
          }}
        />
      )}
      {toast && (
        <div
          style={{
            position: "fixed",
            bottom: 22,
            left: "50%",
            transform: "translateX(-50%)",
            padding: "10px 16px",
            borderRadius: 8,
            background: "var(--as-accent)",
            color: "#04140e",
            fontSize: 12,
            fontWeight: 600,
            zIndex: 200,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}

function ConfirmModal({
  title,
  body,
  confirmLabel,
  danger,
  showPurgeCheckbox,
  onClose,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  showPurgeCheckbox?: boolean;
  onClose: () => void;
  onConfirm: (pin: string, purge: boolean) => Promise<void>;
}) {
  const [pin, setPin] = useState("");
  const [purge, setPurge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!/^\d{4,12}$/.test(pin)) {
      setError("PIN must be 4–12 digits.");
      return;
    }
    setBusy(true);
    try {
      await onConfirm(pin, purge);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
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
        style={{ width: "100%", maxWidth: 420, padding: 22 }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: 12.5, color: "var(--as-ink-3)", marginBottom: 14 }}>{body}</div>
        {showPurgeCheckbox && (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12,
              marginBottom: 12,
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={purge}
              onChange={(e) => setPurge(e.target.checked)}
            />
            Also delete all captured photos (irreversible)
          </label>
        )}
        <label style={{ fontSize: 11, color: "var(--as-ink-3)" }}>
          Enter your PIN to confirm
          <input
            type="password"
            inputMode="numeric"
            autoComplete="current-password"
            className="as-input mono"
            maxLength={12}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
            style={{ width: "100%", marginTop: 4 }}
            autoFocus
          />
        </label>
        {error && (
          <div className="as-banner bad" role="alert" style={{ marginTop: 12, padding: "8px 10px", fontSize: 12 }}>
            {error}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <Button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={busy}
            style={danger ? { background: "var(--as-bad)", color: "#fff" } : undefined}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </form>
    </div>
  );
}

function Row({
  label,
  val,
  mono,
  customVal,
}: {
  label: string;
  val: string;
  mono?: boolean;
  customVal?: React.ReactNode;
}) {
  return (
    <div className="as-stat-row">
      <span className="as-stat-label">{label}</span>
      <span className={`as-stat-val${mono ? " mono" : ""}`}>{customVal ?? val}</span>
    </div>
  );
}
