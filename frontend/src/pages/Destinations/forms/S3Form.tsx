import { FormField, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function S3Form({ config, onChange }: Props) {
  return (
    <>
      <FormField label="Endpoint">
        <TextInput
          className="mono"
          value={String(config.endpoint ?? "")}
          onChange={(e) => onChange("endpoint", e.target.value)}
        />
      </FormField>
      <FormField label="Bucket">
        <TextInput className="mono" value={String(config.bucket ?? "")} onChange={(e) => onChange("bucket", e.target.value)} />
      </FormField>
      <FormField label="Path template" hint="{yyyy} {mm} {dd} {HH} {ts}">
        <TextInput className="mono" value={String(config.prefix ?? "")} onChange={(e) => onChange("prefix", e.target.value)} />
      </FormField>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <FormField label="Access key">
          <TextInput className="mono" placeholder="AKIA…" value={String(config.access_key ?? "")} onChange={(e) => onChange("access_key", e.target.value)} />
        </FormField>
        <FormField label="Secret">
          <TextInput className="mono" type="password" placeholder="••••" value={String(config.secret_key ?? "")} onChange={(e) => onChange("secret_key", e.target.value)} />
        </FormField>
      </div>
    </>
  );
}
