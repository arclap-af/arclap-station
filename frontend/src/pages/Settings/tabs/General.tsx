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
    </div>
  );
}
