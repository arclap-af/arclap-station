import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { FormField, Select, TextInput } from "../components/FormField";
import { Pill, StatusDot } from "../components/Pill";
import { Toggle } from "../components/Toggle";
import { Icon, I } from "../components/icons";
import { schedule, type Schedule as ScheduleType, type ScheduleDraft } from "../lib/bridge/schedule";
import { destinations as destinationsApi } from "../lib/bridge/destinations";

const DAY_LABELS: Array<[ScheduleType["days"][number], string]> = [
  ["mon", "M"],
  ["tue", "T"],
  ["wed", "W"],
  ["thu", "T"],
  ["fri", "F"],
  ["sat", "S"],
  ["sun", "S"],
];

// Available capture-interval choices (minutes). The backend accepts
// any integer from 1 to 1440; this list is the curated UI offering.
// 1 min is included so timelapses of fast subjects (cranes lifting,
// trucks queuing, etc.) can be captured at high enough cadence for
// smooth playback — the camera + uploader pipeline finishes a still
// well within 60 s on the EOS 5D MkIV + local-FS / FTP combo.
const INTERVALS = [1, 5, 10, 15, 30, 60, 120];

export function SchedulePage() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  // Save-flow error surface. Was missing entirely — when a save
  // failed (validation reject, network blip, anything), the modal
  // stayed open and the Save button kept being clickable, but
  // nothing visible happened. Operators reasonably concluded the
  // schedule editor was broken and reverted to "delete + recreate".
  const [saveError, setSaveError] = useState<string | null>(null);
  // Free-text buffer for the custom-interval field so the operator can
  // type multi-digit values (and briefly clear the box) without the
  // draft clamping each keystroke back to a valid number. Committed to
  // the draft on blur; kept in sync when the interval changes elsewhere
  // (preset chip, opening a schedule).
  const [intervalStr, setIntervalStr] = useState("15");
  const draftInterval = draft?.interval_minutes;
  useEffect(() => {
    if (draftInterval != null) setIntervalStr(String(draftInterval));
  }, [draftInterval]);
  const { data: items = [] } = useQuery({ queryKey: ["schedule"], queryFn: schedule.list });
  // Real list of configured destinations — feeds the "Send to" dropdown
  // so the user can route this schedule to a specific destination
  // (or leave it on "All" to fan out to every enabled one). Previously
  // the dropdown was a hardcoded ["All", "S3 only", "Local only"]
  // placeholder that ignored what the user had actually configured.
  const { data: destList = [] } = useQuery({
    queryKey: ["destinations"],
    queryFn: destinationsApi.list,
  });

  const create = useMutation({
    mutationFn: schedule.create,
    onSuccess: () => {
      setSaveError(null);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (e) =>
      setSaveError(e instanceof Error ? e.message : String(e)),
  });
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ScheduleDraft }) => schedule.update(id, payload),
    onSuccess: () => {
      setSaveError(null);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (e) =>
      setSaveError(e instanceof Error ? e.message : String(e)),
  });
  const remove = useMutation({
    mutationFn: schedule.remove,
    onSuccess: () => {
      setSaveError(null);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (e) =>
      setSaveError(e instanceof Error ? e.message : String(e)),
  });
  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => schedule.setEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedule"] }),
  });

  const openExisting = (s: ScheduleType) => {
    setSaveError(null);
    setDraft({
      id: s.id,
      name: s.name,
      interval_minutes: s.interval_minutes,
      from_time: s.from_time,
      to_time: s.to_time,
      days: s.days,
      enabled: s.enabled,
      skip_disk_full: s.skip_disk_full,
      skip_destinations_offline: s.skip_destinations_offline,
      destination_id: s.destination_id,
      destination_label: s.destination_label,
      keep_local: s.keep_local,
    });
  };

  const openNew = () => {
    setSaveError(null);
    setDraft({
      name: "New schedule",
      interval_minutes: 15,
      from_time: "06:00",
      to_time: "19:00",
      days: ["mon", "tue", "wed", "thu", "fri"],
      enabled: true,
      skip_disk_full: true,
      skip_destinations_offline: true,
      destination_id: null,
      destination_label: "All destinations",
      // Default ON — safe. Operator must explicitly opt out of
      // keeping a local copy.
      keep_local: true,
    });
  };

  const toggleDay = (day: ScheduleType["days"][number]) => {
    if (!draft) return;
    setDraft({ ...draft, days: draft.days.includes(day) ? draft.days.filter((d) => d !== day) : [...draft.days, day] });
  };

  const save = () => {
    if (!draft) return;
    if (draft.id) update.mutate({ id: draft.id, payload: draft });
    else create.mutate(draft);
  };

  const capturesPerDay = (s: { interval_minutes: number; from_time: string; to_time: string }) => {
    const [fh] = s.from_time.split(":").map(Number);
    const [th] = s.to_time.split(":").map(Number);
    const hours = ((th - fh + 24) % 24) || 24;
    return Math.max(1, Math.floor((hours * 60) / s.interval_minutes));
  };

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1100 }}>
        <h1 className="as-h1">Schedule</h1>
        <div className="as-h1-sub">
          When this station captures automatically · {items.filter((x) => x.enabled).length} active
        </div>
        <div className="as-grid-2" style={{ alignItems: "start" }}>
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600 }}>
                Configured · {items.length}
              </div>
              <Button variant="primary" onClick={openNew}>
                <Icon d={I.plus} size={14} /> New schedule
              </Button>
            </div>
            {items.map((s) => (
              <div
                key={s.id}
                className="as-card"
                style={{
                  padding: 16,
                  marginBottom: 10,
                  border: draft?.id === s.id ? "1px solid var(--as-accent)" : "1px solid var(--as-line)",
                  cursor: "pointer",
                }}
                onClick={() => openExisting(s)}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                      <Pill tone={s.enabled ? "ok" : "gray"}>
                        <StatusDot tone={s.enabled ? "ok" : "off"} />
                        {s.enabled ? "Active" : "Paused"}
                      </Pill>
                      <div style={{ fontSize: 14.5, fontWeight: 700 }}>{s.name}</div>
                    </div>
                    <div style={{ display: "flex", gap: 14, fontSize: 12, color: "var(--as-ink-3)", flexWrap: "wrap" }}>
                      <span>Every {s.interval_minutes} min</span>
                      <span className="mono">{s.from_time} – {s.to_time}</span>
                      <span>{s.days.length === 7 ? "All days" : `${s.days.length} days`}</span>
                      <span>→ {(() => {
                        // Resolve the schedule's destination_id to a
                        // friendly name from the live destinations list
                        // so the row shows "New FTP destination · FTP"
                        // instead of the raw UUID the backend persists
                        // in dest_filter. Falls back to the bridge-side
                        // label if the destination has been deleted
                        // (UUID still in the schedule, no live match).
                        if (!s.destination_id) return "All destinations";
                        const d = destList.find((x) => x.id === s.destination_id);
                        if (d) return `${d.name} · ${d.kind.toUpperCase()}`;
                        return s.destination_label;
                      })()}</span>
                    </div>
                  </div>
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                    }}
                  >
                    <Toggle on={s.enabled} onChange={(next) => setEnabled.mutate({ id: s.id, enabled: next })} />
                  </div>
                </div>
              </div>
            ))}
            {items.length === 0 && (
              <div className="as-card" style={{ padding: 0 }}>
                <EmptyState
                  icon={I.schedule}
                  title="No schedules yet"
                  message="Create a schedule to capture automatically at a set interval during your active hours."
                  action={
                    <Button variant="primary" style={{ padding: "8px 16px", fontSize: 13 }} onClick={openNew}>
                      <Icon d={I.plus} size={14} /> New schedule
                    </Button>
                  }
                />
              </div>
            )}
          </div>

          <div style={{ position: "sticky", top: 0 }}>
            {!draft ? (
              <div className="as-card" style={{ padding: 40, textAlign: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Select a schedule</div>
                <Button onClick={openNew}>+ New schedule</Button>
              </div>
            ) : (
              <div className="as-card" style={{ padding: 0 }}>
                <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between" }}>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>{draft.id ? "Editing" : "New schedule"}</div>
                  <button
                    type="button"
                    onClick={() => setDraft(null)}
                    aria-label="Close"
                    className="as-btn-icon"
                    style={{ width: 32, height: 32 }}
                  >
                    ×
                  </button>
                </div>
                <div style={{ padding: 20 }}>
                  <FormField label="Name">
                    <TextInput value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </FormField>
                  <FormField label="Interval" hint="Pick a preset or type any value (1–1440 min).">
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                      {INTERVALS.map((m) => {
                        const active = draft.interval_minutes === m;
                        return (
                          <button
                            key={m}
                            type="button"
                            onClick={() => setDraft({ ...draft, interval_minutes: m })}
                            style={{
                              padding: "7px 14px",
                              borderRadius: 8,
                              border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
                              background: active ? "var(--as-accent)" : "var(--as-surface)",
                              color: active ? "#04140e" : "var(--as-ink)",
                              fontWeight: active ? 700 : 500,
                              fontSize: 12.5,
                              cursor: "pointer",
                              fontFamily: "inherit",
                            }}
                          >
                            {m < 60 ? `${m} min` : m === 60 ? "1 h" : `${m / 60} h`}
                          </button>
                        );
                      })}
                      {/* Custom interval — the backend takes any integer 1–1440,
                          so an operator isn't boxed into the presets. Highlighted
                          when the current value isn't one of the presets. */}
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          padding: "3px 8px 3px 10px",
                          borderRadius: 8,
                          border: `1px solid ${INTERVALS.includes(draft.interval_minutes) ? "var(--as-line)" : "var(--as-accent)"}`,
                          background: "var(--as-surface)",
                        }}
                      >
                        <span style={{ fontSize: 11.5, color: "var(--as-ink-3)" }}>Custom</span>
                        <input
                          type="number"
                          min={1}
                          max={1440}
                          step={1}
                          value={intervalStr}
                          aria-label="Custom interval in minutes"
                          // Type freely; clamp + commit to the draft on blur
                          // (or Enter). Empty/invalid falls back to 1.
                          onChange={(e) => setIntervalStr(e.target.value)}
                          onBlur={() => {
                            const n = Math.round(Number(intervalStr));
                            const clamped = Number.isFinite(n) ? Math.max(1, Math.min(1440, n)) : 1;
                            setIntervalStr(String(clamped));
                            setDraft({ ...draft, interval_minutes: clamped });
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                          }}
                          className="mono"
                          style={{
                            width: 58,
                            height: 30,
                            textAlign: "center",
                            fontSize: 12.5,
                            borderRadius: 6,
                            border: "1px solid var(--as-line)",
                            background: "var(--as-bg-2)",
                            color: "var(--as-ink)",
                            fontFamily: "var(--as-mono)",
                          }}
                        />
                        <span style={{ fontSize: 11.5, color: "var(--as-ink-3)" }}>min</span>
                      </div>
                    </div>
                  </FormField>
                  <FormField label="Active hours">
                    <div className="as-form-row-2">
                      <TextInput type="time" value={draft.from_time} onChange={(e) => setDraft({ ...draft, from_time: e.target.value })} />
                      <TextInput type="time" value={draft.to_time} onChange={(e) => setDraft({ ...draft, to_time: e.target.value })} />
                    </div>
                  </FormField>
                  <FormField label="Days">
                    <div style={{ display: "flex", gap: 6 }}>
                      {DAY_LABELS.map(([d, l]) => {
                        const active = draft.days.includes(d);
                        return (
                          <button
                            key={d}
                            type="button"
                            onClick={() => toggleDay(d)}
                            style={{
                              flex: 1,
                              height: 38,
                              borderRadius: 8,
                              border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
                              background: active ? "var(--as-accent-soft)" : "var(--as-surface)",
                              color: active ? "var(--as-accent-2)" : "var(--as-ink-3)",
                              fontWeight: 700,
                              fontSize: 13,
                              cursor: "pointer",
                              fontFamily: "inherit",
                            }}
                          >
                            {l}
                          </button>
                        );
                      })}
                    </div>
                  </FormField>
                  <FormField label="Send to">
                    <Select
                      value={draft.destination_id ?? ""}
                      onChange={(e) => {
                        // Empty value (the "All destinations" row) → null
                        // dest_filter on the backend, which fans out to
                        // every enabled destination. Otherwise the value
                        // IS the destination ID; we also stash a
                        // human-readable label for the UI's list view.
                        const id = e.target.value || null;
                        const label = id
                          ? destList.find((d) => d.id === id)?.name ?? id
                          : "All destinations";
                        setDraft({ ...draft, destination_id: id, destination_label: label });
                      }}
                    >
                      <option value="">All destinations</option>
                      {destList.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name} · {d.kind.toUpperCase()}
                          {d.enabled ? "" : " (disabled)"}
                        </option>
                      ))}
                    </Select>
                  </FormField>
                  <div style={{ padding: "12px 14px", border: "1px solid var(--as-line)", borderRadius: 8, background: "var(--as-bg-2)", marginBottom: 14 }}>
                    <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600, marginBottom: 10 }}>
                      Skip when
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0" }}>
                      <div style={{ fontSize: 13 }}>Disk &gt; 90%</div>
                      <Toggle on={draft.skip_disk_full} onChange={(v) => setDraft({ ...draft, skip_disk_full: v })} />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderTop: "1px solid var(--as-line)" }}>
                      <div style={{ fontSize: 13 }}>Destinations offline</div>
                      <Toggle on={draft.skip_destinations_offline} onChange={(v) => setDraft({ ...draft, skip_destinations_offline: v })} />
                    </div>
                  </div>
                  <div style={{ padding: "12px 14px", border: "1px solid var(--as-line)", borderRadius: 8, background: "var(--as-bg-2)", marginBottom: 14 }}>
                    <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.06, fontWeight: 600, marginBottom: 10 }}>
                      After upload
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0" }}>
                      <div style={{ flex: 1, paddingRight: 16 }}>
                        <div style={{ fontSize: 13 }}>Keep local copy</div>
                        <div style={{ fontSize: 11, color: "var(--as-ink-3)", marginTop: 2, lineHeight: 1.5 }}>
                          {draft.keep_local
                            ? "Photo stays on the SD card after upload. Safe default — both copies exist."
                            : "Photo is deleted from the SD card after EVERY destination uploads successfully."}
                        </div>
                      </div>
                      <Toggle on={draft.keep_local} onChange={(v) => setDraft({ ...draft, keep_local: v })} />
                    </div>
                    {/* Offline-fallback explainer. Every capture lands
                        on the SD card BEFORE the upload attempt — there
                        is no "no internet → no photo" failure mode.
                        Surfacing this in the form means an operator
                        with a flaky 4G link can reason about what
                        happens to a photo when the link is down. */}
                    <div
                      style={{
                        marginTop: 10,
                        padding: "8px 10px",
                        borderRadius: 6,
                        background: "color-mix(in srgb, var(--as-accent-2) 8%, transparent)",
                        fontSize: 11,
                        color: "var(--as-ink-2)",
                        lineHeight: 1.5,
                      }}
                    >
                      <strong style={{ color: "var(--as-accent-2)" }}>If the internet / FTP is down:</strong>{" "}
                      every photo is written to the SD card FIRST, then
                      queued for upload. If a destination is
                      unreachable, the photo stays in the queue and
                      retries with exponential backoff until success.
                      The local copy is{" "}
                      {draft.keep_local ? "kept either way" : "only deleted once every destination has acknowledged the upload"}.
                    </div>
                  </div>
                  <div style={{ padding: "10px 14px", background: "var(--as-accent-soft)", borderRadius: 8, fontSize: 12, color: "var(--as-accent-2)" }}>
                    ~<strong>{capturesPerDay(draft)}</strong> captures/day ·{" "}
                    <strong>{capturesPerDay(draft) * draft.days.length}</strong>/week
                  </div>
                  {saveError && (
                    <div
                      role="alert"
                      style={{
                        marginTop: 12,
                        padding: "10px 14px",
                        borderRadius: 8,
                        border: "1px solid var(--as-bad)",
                        background: "color-mix(in srgb, var(--as-bad) 12%, transparent)",
                        color: "var(--as-bad)",
                        fontSize: 12.5,
                      }}
                    >
                      <strong>Save failed</strong>
                      <div style={{ marginTop: 4, fontFamily: "var(--as-mono)" }}>
                        {saveError}
                      </div>
                    </div>
                  )}
                </div>
                <div style={{ padding: 14, borderTop: "1px solid var(--as-line)", display: "flex", justifyContent: "space-between", background: "var(--as-bg-2)" }}>
                  {draft.id ? (
                    <Button style={{ color: "var(--as-bad)" }} onClick={() => remove.mutate(draft.id!)}>
                      Delete
                    </Button>
                  ) : (
                    <div />
                  )}
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button onClick={() => setDraft(null)}>Cancel</Button>
                    <Button variant="primary" onClick={save} disabled={create.isPending || update.isPending}>
                      Save
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
