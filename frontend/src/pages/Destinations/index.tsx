import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { FormField, TextInput } from "../../components/FormField";
import { Pill, StatusDot } from "../../components/Pill";
import { Toggle } from "../../components/Toggle";
import { Icon, I } from "../../components/icons";
import { destinations, type Destination, type DestinationDraft, type DestinationKind, type DestinationTest } from "../../lib/bridge/destinations";

import { S3Form } from "./forms/S3Form";
import { SFTPForm } from "./forms/SFTPForm";
import { FTPForm } from "./forms/FTPForm";
import { WebhookForm } from "./forms/WebhookForm";
import { LocalForm } from "./forms/LocalForm";
import { MQTTForm } from "./forms/MQTTForm";

interface TypeMeta {
  id: DestinationKind;
  name: string;
  hint: string;
  color: string;
}

const TYPES: TypeMeta[] = [
  { id: "s3", name: "AWS S3 / S3-compatible", hint: "Push to bucket as the photo lands", color: "#10b981" },
  { id: "sftp", name: "SFTP", hint: "Upload via SSH", color: "#3b82f6" },
  { id: "ftp", name: "FTP / FTPS", hint: "Classic FTP, plain or TLS", color: "#06b6d4" },
  { id: "webhook", name: "HTTPS webhook", hint: "POST to a URL", color: "#a855f7" },
  { id: "local", name: "Local USB / NAS", hint: "Mount and copy", color: "#f59e0b" },
  { id: "mqtt", name: "MQTT", hint: "Publish metadata to a broker", color: "#10b981" },
];

export function Destinations() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<DestinationDraft | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [testResult, setTestResult] = useState<DestinationTest | null>(null);
  const [testRanOk, setTestRanOk] = useState(false);

  const { data: items = [] } = useQuery({ queryKey: ["destinations"], queryFn: destinations.list, refetchInterval: 8000 });

  const test = useMutation({
    mutationFn: destinations.test,
    onSuccess: (r) => {
      setTestResult(r);
      setTestRanOk(r.ok);
    },
    onError: (err) => {
      setTestResult({ ok: false, steps: [{ label: "request", ok: false, detail: err instanceof Error ? err.message : null }] });
      setTestRanOk(false);
    },
  });
  const create = useMutation({
    mutationFn: destinations.create,
    onSuccess: () => {
      setDraft(null);
      setTestResult(null);
      setTestRanOk(false);
      qc.invalidateQueries({ queryKey: ["destinations"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: DestinationDraft }) => destinations.update(id, payload),
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["destinations"] });
    },
  });
  const remove = useMutation({
    mutationFn: destinations.remove,
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["destinations"] });
    },
  });
  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => destinations.setEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["destinations"] }),
  });

  useEffect(() => {
    setTestRanOk(false);
    setTestResult(null);
  }, [draft?.kind, draft?.config, draft?.id]);

  const onOpen = (d: Destination) => {
    setPickerOpen(false);
    setDraft({
      id: d.id,
      kind: d.kind,
      name: d.name,
      enabled: d.enabled,
      config: d.config,
      retry_policy: d.retry_policy,
      encrypt_in_transit: d.encrypt_in_transit,
    });
    // Force a fresh Test before saving an edited destination. Was
    // previously `true` (auto-pass) which let the operator hit Save
    // even after changing values that would break uploads — and
    // because secrets come back as bullet sentinels, that path was
    // the trigger for accidental credential corruption.
    setTestRanOk(false);
    setTestResult(null);
  };

  // Type-specific config defaults. The forms used to show these via
  // JSX `value={config.x ?? "DEFAULT"}` fallbacks, but the fallback
  // ONLY affected the visible input — config itself stayed empty,
  // and clicking Test sent `{}` to the backend which raised
  // ValueError("uploader requires 'X'") and FastAPI surfaced it as
  // HTTP 500. Pre-seeding here means whatever the operator sees in
  // the form IS what gets sent.
  const DEFAULT_CONFIG: Record<DestinationKind, Record<string, unknown>> = {
    s3: {
      endpoint: "https://s3.eu-central-1.amazonaws.com",
      // eu-central-1 is the project's primary region (Swiss/EU data
      // residency, CLAUDE.md). The form now exposes Region so this is
      // just the starting value, not a hard pin.
      region: "eu-central-1",
      bucket: "",
      access_key: "",
      secret_key: "",
      prefix: "photos/",
    },
    sftp: {
      host: "",
      port: "22",
      user: "",
      auth: "password",
      password: "",
      private_key: "",
      private_key_passphrase: "",
      remote_path: "/photos/",
    },
    ftp: {
      host: "",
      port: "21",
      user: "",
      password: "",
      mode: "passive",
      remote_path: "/photos/",
      security: "plain",
    },
    local: {
      // Default to a path that's writable out-of-the-box under the
      // arclap-station systemd unit. `/media/...` requires both a
      // mounted USB stick AND a ReadWritePaths extension (we now
      // ship one — see systemd unit), but the default needs to
      // work zero-config so the operator can verify the capture →
      // upload pipeline without first plugging anything in.
      path: "/var/lib/arclap/local-photos",
      when_full: "stop",
    },
    webhook: {
      url: "",
      method: "POST",
      timeout_seconds: "10",
    },
    mqtt: {
      // MQTTForm reads config.broker (a URL) + config.topic +
      // config.client_id. Earlier defaults seeded `host` / `port` /
      // `username` / `password` which the form never reads — so the
      // operator saw the form's `??` placeholders but the actual
      // config was empty, causing the same class of silent
      // misconfiguration the local destination had.
      broker: "mqtts://broker.example.com:8883",
      topic: "arclap/{station}/photos",
      client_id: "",
    },
  };

  const startNew = (kind: DestinationKind) => {
    setPickerOpen(false);
    setTestRanOk(false);
    setTestResult(null);
    setDraft({
      kind,
      name: `New ${kind.toUpperCase()} destination`,
      enabled: true,
      // Spread the type-specific defaults so the form's placeholders
      // are also what gets POSTed to /api/destinations/test on the
      // first click — no more silent 500s when the operator just
      // wants to verify the suggested defaults work.
      config: { ...(DEFAULT_CONFIG[kind] ?? {}) },
      retry_policy: 3,
      encrypt_in_transit: true,
    });
  };

  const onConfigChange = (k: string, v: string) => {
    if (!draft) return;
    setDraft({ ...draft, config: { ...draft.config, [k]: v } });
  };

  const save = () => {
    if (!draft) return;
    if (draft.id) update.mutate({ id: draft.id, payload: draft });
    else create.mutate(draft);
  };

  const meta = TYPES.find((t) => t.id === draft?.kind);

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1100 }}>
        <h1 className="as-h1">Destinations</h1>
        <div className="as-h1-sub">
          Where photos go after capture · {items.filter((x) => x.enabled).length} active of {items.length}
        </div>

        <div className="as-grid-2" style={{ alignItems: "start" }}>
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600 }}>
                Configured · {items.length}
              </div>
              <Button
                variant="primary"
                onClick={() => {
                  setPickerOpen(true);
                  setDraft(null);
                }}
              >
                + Add destination
              </Button>
            </div>
            {items.map((d) => (
              <div
                key={d.id}
                className="as-card"
                style={{
                  padding: 14,
                  marginBottom: 10,
                  border: draft?.id === d.id ? "1px solid var(--as-accent)" : "1px solid var(--as-line)",
                  cursor: "pointer",
                }}
                onClick={() => onOpen(d)}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                  <div
                    style={{
                      width: 36,
                      height: 36,
                      borderRadius: 8,
                      background: `${TYPES.find((t) => t.id === d.kind)?.color ?? "#10b981"}22`,
                      color: TYPES.find((t) => t.id === d.kind)?.color ?? "#10b981",
                      display: "grid",
                      placeItems: "center",
                      flexShrink: 0,
                      fontWeight: 700,
                      fontSize: 12,
                    }}
                  >
                    {d.kind.toUpperCase().slice(0, 4)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                      <Pill tone={d.enabled ? "ok" : "gray"}>
                        <StatusDot tone={d.enabled ? "ok" : "off"} />
                        {d.enabled ? "Active" : "Paused"}
                      </Pill>
                      <div style={{ fontSize: 13.5, fontWeight: 700 }}>{d.name}</div>
                    </div>
                    <div className="mono" style={{ color: "var(--as-ink-3)", fontSize: 11, marginBottom: 6 }}>
                      {String(d.config.endpoint ?? d.config.host ?? d.config.url ?? "—")}
                    </div>
                    <div style={{ display: "flex", gap: 14, fontSize: 11.5, color: "var(--as-ink-3)" }}>
                      <span>
                        Last sync: <strong style={{ color: "var(--as-ink)" }}>{d.last_sync ?? "never"}</strong>
                      </span>
                      <span>
                        Queue: <strong style={{ color: d.queue_pending ? "var(--as-warn)" : "var(--as-ink)" }}>{d.queue_pending}</strong>
                      </span>
                      <span>
                        Failed: <strong style={{ color: d.queue_failed ? "var(--as-bad)" : "var(--as-ink)" }}>{d.queue_failed}</strong>
                      </span>
                    </div>
                  </div>
                  <div onClick={(e) => e.stopPropagation()}>
                    <Toggle on={d.enabled} onChange={(v) => setEnabled.mutate({ id: d.id, enabled: v })} />
                  </div>
                </div>
              </div>
            ))}
            {items.length === 0 && !pickerOpen && (
              <div className="as-card" style={{ padding: 0 }}>
                <EmptyState
                  icon={I.upload}
                  title="No destinations yet"
                  message="Photos stay on the Pi until you add a destination. Add an FTP, SFTP, S3, local disk, webhook, or MQTT target to start uploading."
                  action={
                    <Button variant="primary" style={{ padding: "8px 16px", fontSize: 13 }} onClick={() => setPickerOpen(true)}>
                      <Icon d={I.plus} size={14} /> Add destination
                    </Button>
                  }
                />
              </div>
            )}
          </div>

          <div style={{ position: "sticky", top: 0 }}>
            {pickerOpen && (
              <div className="as-card" style={{ padding: 0 }}>
                <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between" }}>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>Choose type</div>
                  <button type="button" onClick={() => setPickerOpen(false)} className="as-btn-icon" aria-label="Close" style={{ width: 32, height: 32 }}>
                    ×
                  </button>
                </div>
                <div style={{ padding: 16 }}>
                  {TYPES.map((t) => (
                    <div
                      key={t.id}
                      onClick={() => startNew(t.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => e.key === "Enter" && startNew(t.id)}
                      style={{
                        padding: "12px 14px",
                        borderRadius: 8,
                        marginBottom: 6,
                        border: "1px solid var(--as-line)",
                        cursor: "pointer",
                        display: "flex",
                        gap: 12,
                        alignItems: "flex-start",
                      }}
                    >
                      <div
                        style={{
                          width: 34,
                          height: 34,
                          borderRadius: 8,
                          background: `${t.color}22`,
                          color: t.color,
                          display: "grid",
                          placeItems: "center",
                          flexShrink: 0,
                          fontWeight: 700,
                          fontSize: 11,
                        }}
                      >
                        {t.id.toUpperCase().slice(0, 4)}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 13.5, fontWeight: 600 }}>{t.name}</div>
                        <div style={{ fontSize: 11.5, color: "var(--as-ink-3)", marginTop: 2 }}>{t.hint}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!pickerOpen && !draft && (
              <div className="as-card" style={{ padding: 40, textAlign: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Select a destination</div>
                <Button onClick={() => setPickerOpen(true)}>+ Add destination</Button>
              </div>
            )}

            {draft && (
              <div className="as-card" style={{ padding: 0 }}>
                <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600 }}>
                      {draft.id ? "Editing" : "New"} · {meta?.name}
                    </div>
                    <div style={{ fontSize: 15, fontWeight: 700, marginTop: 1 }}>{draft.name}</div>
                  </div>
                  <button type="button" onClick={() => setDraft(null)} className="as-btn-icon" aria-label="Close" style={{ width: 32, height: 32 }}>
                    ×
                  </button>
                </div>
                <div style={{ padding: 20 }}>
                  <FormField label="Name">
                    <TextInput value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </FormField>
                  {draft.kind === "s3" && <S3Form config={draft.config} onChange={onConfigChange} />}
                  {draft.kind === "sftp" && <SFTPForm config={draft.config} onChange={onConfigChange} />}
                  {draft.kind === "ftp" && <FTPForm config={draft.config} onChange={onConfigChange} />}
                  {draft.kind === "webhook" && <WebhookForm config={draft.config} onChange={onConfigChange} />}
                  {draft.kind === "local" && <LocalForm config={draft.config} onChange={onConfigChange} />}
                  {draft.kind === "mqtt" && <MQTTForm config={draft.config} onChange={onConfigChange} />}

                  <div style={{ padding: "12px 14px", border: "1px solid var(--as-line)", borderRadius: 8, background: "var(--as-bg-2)", marginTop: 6 }}>
                    <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600, marginBottom: 10 }}>
                      Delivery
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0" }}>
                      <div style={{ fontSize: 13 }}>Encrypt in transit</div>
                      <Toggle on={draft.encrypt_in_transit} onChange={(v) => setDraft({ ...draft, encrypt_in_transit: v })} />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderTop: "1px solid var(--as-line)" }}>
                      <div style={{ fontSize: 13 }}>Retry on failure</div>
                      <select
                        className="as-select"
                        value={draft.retry_policy}
                        onChange={(e) => setDraft({ ...draft, retry_policy: parseInt(e.target.value, 10) })}
                        style={{ width: 90 }}
                      >
                        <option value={0}>Off</option>
                        <option value={3}>3×</option>
                        <option value={5}>5×</option>
                        <option value={10}>10×</option>
                      </select>
                    </div>
                  </div>

                  {testResult && (
                    <div className={`as-banner ${testResult.ok ? "" : "bad"}`} style={{ marginTop: 14, marginBottom: 0 }}>
                      <div>
                        <div style={{ fontWeight: 700 }}>{testResult.ok ? "Test succeeded" : "Test failed"}</div>
                        <ul style={{ margin: "6px 0 0 18px", padding: 0, fontSize: 11.5 }}>
                          {testResult.steps.map((s, i) => (
                            <li key={i} style={{ color: s.ok ? "var(--as-accent-2)" : "var(--as-bad)" }}>
                              {s.label}
                              {s.detail ? ` — ${s.detail}` : ""}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  )}
                </div>
                <div style={{ padding: 14, borderTop: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between", background: "var(--as-bg-2)" }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <Button onClick={() => test.mutate(draft)} disabled={test.isPending}>
                      {test.isPending ? "Testing…" : "Test"}
                    </Button>
                    {draft.id && (
                      <Button style={{ color: "var(--as-bad)" }} onClick={() => remove.mutate(draft.id!)}>
                        Delete
                      </Button>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button onClick={() => setDraft(null)}>Cancel</Button>
                    <Button
                      variant="primary"
                      onClick={save}
                      disabled={!testRanOk || create.isPending || update.isPending}
                      title={!testRanOk ? "Run Test successfully before saving" : "Save"}
                    >
                      {draft.id ? "Save" : "Create"}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
