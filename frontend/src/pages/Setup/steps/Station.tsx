import { FormField, Select, TextInput } from "../../../components/FormField";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

export function Station({ state, update }: Props) {
  return (
    <>
      <FormField label="Friendly name" hint="Used in cockpit and EXIF metadata">
        <TextInput value={state.stationName} onChange={(e) => update("stationName", e.target.value)} />
      </FormField>
      <FormField label="Timezone" className="!mb-0">
        <Select value={state.timezone} onChange={(e) => update("timezone", e.target.value)}>
          <option>Europe/Zurich</option>
          <option>Europe/Berlin</option>
          <option>Europe/London</option>
          <option>UTC</option>
        </Select>
      </FormField>
    </>
  );
}
