import { FormField, TextInput } from "../../../components/FormField";
import { Icon, I } from "../../../components/icons";
import type { SetupState } from "..";

interface Props {
  state: SetupState;
  update: <K extends keyof SetupState>(k: K, v: SetupState[K]) => void;
}

const TYPES: Array<[SetupState["destType"], string, string]> = [
  ["s3", "S3", I.cloud],
  ["sftp", "SFTP", I.upload],
  ["ftp", "FTP / FTPS", I.upload],
  ["arc", "Arclap Cloud", I.zap],
];

export function Destination({ state, update }: Props) {
  const setEndpoint = (k: string, v: string) =>
    update("destConfig", { ...state.destConfig, [k]: v });

  return (
    <>
      <FormField label="Destination type">
        <div className="as-form-row-2" style={{ marginTop: 4 }}>
          {TYPES.map(([id, name, ic]) => {
            const active = state.destType === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => update("destType", id)}
                style={{
                  padding: "10px 12px",
                  borderRadius: 8,
                  border: `1px solid ${active ? "var(--as-accent)" : "var(--as-line)"}`,
                  background: active ? "var(--as-accent-soft)" : "transparent",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  fontFamily: "inherit",
                  color: "var(--as-ink)",
                }}
              >
                <Icon d={ic} size={16} style={{ color: active ? "var(--as-accent-2)" : "var(--as-ink-3)" }} />
                <span style={{ fontSize: 13, fontWeight: 600 }}>{name}</span>
              </button>
            );
          })}
        </div>
      </FormField>
      <FormField label="Name">
        <TextInput value={state.destName} onChange={(e) => update("destName", e.target.value)} />
      </FormField>
      <FormField label="Bucket / endpoint" className="!mb-0">
        <TextInput
          className="mono"
          placeholder="my-bucket"
          value={String(state.destConfig.endpoint ?? state.destConfig.bucket ?? "")}
          onChange={(e) => setEndpoint("endpoint", e.target.value)}
        />
      </FormField>
    </>
  );
}
