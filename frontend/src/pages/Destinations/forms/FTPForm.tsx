import { FormField, Select, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function FTPForm({ config, onChange }: Props) {
  return (
    <>
      <FormField label="Host">
        <TextInput className="mono" value={String(config.host ?? "")} onChange={(e) => onChange("host", e.target.value)} />
      </FormField>
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 10 }}>
        <FormField label="User">
          <TextInput className="mono" value={String(config.user ?? "")} onChange={(e) => onChange("user", e.target.value)} />
        </FormField>
        <FormField label="Port">
          <TextInput className="mono" value={String(config.port ?? "21")} onChange={(e) => onChange("port", e.target.value)} />
        </FormField>
        <FormField label="Mode">
          <Select value={String(config.mode ?? "passive")} onChange={(e) => onChange("mode", e.target.value)}>
            <option value="passive">Passive</option>
            <option value="active">Active</option>
          </Select>
        </FormField>
      </div>
      <FormField label="Password">
        <TextInput className="mono" type="password" value={String(config.password ?? "")} onChange={(e) => onChange("password", e.target.value)} />
      </FormField>
      <FormField label="Remote path">
        <TextInput className="mono" value={String(config.remote_path ?? "/photos/{yyyy}/{mm}/{dd}/")} onChange={(e) => onChange("remote_path", e.target.value)} />
      </FormField>
      <FormField label="Security">
        <Select value={String(config.security ?? "ftps_explicit")} onChange={(e) => onChange("security", e.target.value)}>
          <option value="ftps_explicit">FTPS explicit</option>
          <option value="ftps_implicit">FTPS implicit</option>
          <option value="plain">Plain FTP</option>
        </Select>
      </FormField>
    </>
  );
}
