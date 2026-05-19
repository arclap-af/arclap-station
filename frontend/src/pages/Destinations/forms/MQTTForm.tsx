import { FormField, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function MQTTForm({ config, onChange }: Props) {
  return (
    <>
      <FormField label="Broker URL" hint="mqtts://broker:8883">
        <TextInput
          className="mono"
          value={String(config.broker ?? "")}
          onChange={(e) => onChange("broker", e.target.value)}
        />
      </FormField>
      <FormField label="Topic template">
        <TextInput
          className="mono"
          value={String(config.topic ?? "arclap/{station}/photos")}
          onChange={(e) => onChange("topic", e.target.value)}
        />
      </FormField>
      <FormField label="Client ID">
        <TextInput className="mono" value={String(config.client_id ?? "")} onChange={(e) => onChange("client_id", e.target.value)} />
      </FormField>
    </>
  );
}
