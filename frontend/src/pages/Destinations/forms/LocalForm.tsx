import { FormField, Select, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function LocalForm({ config, onChange }: Props) {
  return (
    <>
      <FormField label="Mount point">
        <TextInput
          className="mono"
          value={String(config.path ?? "/media/usb-photos")}
          onChange={(e) => onChange("path", e.target.value)}
        />
      </FormField>
      <FormField label="When full">
        <Select value={String(config.when_full ?? "stop")} onChange={(e) => onChange("when_full", e.target.value)}>
          <option value="stop">Stop · alert</option>
          <option value="overwrite">Overwrite oldest</option>
        </Select>
      </FormField>
    </>
  );
}
