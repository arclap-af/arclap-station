import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { FormField, Select, TextInput } from "../../../components/FormField";
import { settings, type GeneralSettings } from "../../../lib/bridge/settings";

export function General() {
  const { data, refetch } = useQuery({ queryKey: ["settings.general"], queryFn: settings.general });
  const [draft, setDraft] = useState<GeneralSettings | null>(null);
  useEffect(() => {
    if (data) setDraft(data);
  }, [data]);

  const save = useMutation({
    mutationFn: (patch: Partial<GeneralSettings>) => settings.saveGeneral(patch),
    onSuccess: () => refetch(),
  });

  if (!draft) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Identity</div>
        <FormField label="Station name" hint="Used in EXIF and cockpit">
          <TextInput value={draft.station_name} onChange={(e) => setDraft({ ...draft, station_name: e.target.value })} />
        </FormField>
        <FormField label="Site / project">
          <TextInput value={draft.site} onChange={(e) => setDraft({ ...draft, site: e.target.value })} />
        </FormField>
        <FormField label="GPS coordinates">
          <TextInput className="mono" value={draft.gps} onChange={(e) => setDraft({ ...draft, gps: e.target.value })} />
        </FormField>
        <FormField label="Asset tag">
          <TextInput className="mono" value={draft.asset_tag} onChange={(e) => setDraft({ ...draft, asset_tag: e.target.value })} />
        </FormField>
        <Button variant="primary" onClick={() => save.mutate(draft)} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Locale</div>
        <FormField label="Timezone">
          <Select value={draft.timezone} onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}>
            <option>Europe/Zurich</option>
            <option>Europe/Berlin</option>
            <option>UTC</option>
          </Select>
        </FormField>
        <FormField label="Date format">
          <Select value={draft.date_format} onChange={(e) => setDraft({ ...draft, date_format: e.target.value })}>
            <option>YYYY-MM-DD</option>
            <option>DD.MM.YYYY</option>
          </Select>
        </FormField>
        <FormField label="Language">
          <Select value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })}>
            <option>English</option>
            <option>Deutsch</option>
            <option>Français</option>
          </Select>
        </FormField>
        <FormField label="NTP servers">
          <TextInput className="mono" value={draft.ntp_servers} onChange={(e) => setDraft({ ...draft, ntp_servers: e.target.value })} />
        </FormField>
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Photo & project (v0.8)</div>
        <FormField label="Burn watermark into JPEGs" hint="Bottom-right: serial · site · UTC timestamp.">
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <input
              type="checkbox"
              checked={draft.watermark}
              onChange={(e) => setDraft({ ...draft, watermark: e.target.checked })}
            />
            <span>Enable watermark</span>
          </label>
        </FormField>
        <FormField
          label="Dedup threshold (Hamming distance)"
          hint="0 = exact-match only, 4–6 = visually identical, blank = off. Drops near-identical frames in a 10-min window."
        >
          <TextInput
            className="mono"
            type="number"
            placeholder="off"
            value={draft.dedup_threshold ?? ""}
            onChange={(e) => setDraft({
              ...draft,
              dedup_threshold: e.target.value === "" ? null : Number(e.target.value),
            })}
          />
        </FormField>
        <FormField label="Upload rate cap (kbps)" hint="Blank = unlimited. Prevents saturating site Wi-Fi during work hours.">
          <TextInput
            className="mono"
            type="number"
            placeholder="unlimited"
            value={draft.bandwidth_kbps ?? ""}
            onChange={(e) => setDraft({
              ...draft,
              bandwidth_kbps: e.target.value === "" ? null : Number(e.target.value),
            })}
          />
        </FormField>
        <FormField label="Project start (ISO date)" hint="Informational; surfaces in audit + cockpit.">
          <TextInput
            className="mono"
            placeholder="2026-04-01"
            value={draft.project_starts_at ?? ""}
            onChange={(e) => setDraft({
              ...draft,
              project_starts_at: e.target.value || null,
            })}
          />
        </FormField>
        <FormField label="Project end (ISO date)" hint="Photos auto-purge after this date (future feature).">
          <TextInput
            className="mono"
            placeholder="2027-12-31"
            value={draft.project_ends_at ?? ""}
            onChange={(e) => setDraft({
              ...draft,
              project_ends_at: e.target.value || null,
            })}
          />
        </FormField>
        <Button variant="primary" onClick={() => save.mutate(draft)} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save photo + project"}
        </Button>
      </div>
    </div>
  );
}
