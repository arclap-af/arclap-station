import { FormField, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function WebhookForm({ config, onChange }: Props) {
  return (
    <>
      <FormField label="URL">
        <TextInput
          className="mono"
          placeholder="https://example.com/hooks/arclap"
          value={String(config.url ?? "")}
          onChange={(e) => onChange("url", e.target.value)}
        />
      </FormField>
      <FormField label="Authorization header">
        <TextInput
          className="mono"
          placeholder="Bearer …"
          value={String(config.auth_header ?? "")}
          onChange={(e) => onChange("auth_header", e.target.value)}
        />
      </FormField>
    </>
  );
}
