import { FormField, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function SFTPForm({ config, onChange }: Props) {
  return (
    <>
      <FormField label="Host">
        <TextInput className="mono" value={String(config.host ?? "")} onChange={(e) => onChange("host", e.target.value)} />
      </FormField>
      <div className="as-form-row-2-1">
        <FormField label="User">
          <TextInput className="mono" value={String(config.user ?? "")} onChange={(e) => onChange("user", e.target.value)} />
        </FormField>
        <FormField label="Port">
          <TextInput className="mono" value={String(config.port ?? "22")} onChange={(e) => onChange("port", e.target.value)} />
        </FormField>
      </div>
      <FormField label="Password / passphrase">
        <TextInput className="mono" type="password" value={String(config.password ?? "")} onChange={(e) => onChange("password", e.target.value)} />
      </FormField>
      <FormField label="Remote path">
        <TextInput className="mono" value={String(config.remote_path ?? "/photos/{yyyy}/{mm}/{dd}/")} onChange={(e) => onChange("remote_path", e.target.value)} />
      </FormField>
    </>
  );
}
