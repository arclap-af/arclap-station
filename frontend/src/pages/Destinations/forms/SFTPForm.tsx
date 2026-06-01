import { FormField, TextInput } from "../../../components/FormField";

interface Props {
  config: Record<string, unknown>;
  onChange: (k: string, v: string) => void;
}

export function SFTPForm({ config, onChange }: Props) {
  // `auth` is a UI affordance only — the backend uses whichever
  // credential is present. Infer it for existing destinations that
  // were saved before this selector existed.
  const auth = String(config.auth ?? (config.private_key ? "key" : "password"));
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
      <FormField label="Authentication">
        <select className="as-select" value={auth} onChange={(e) => onChange("auth", e.target.value)} style={{ width: "100%" }}>
          <option value="password">Password</option>
          <option value="key">Private key</option>
        </select>
      </FormField>
      {auth === "key" ? (
        <>
          <FormField label="Private key (PEM)" hint="paste the full -----BEGIN … KEY----- block">
            <textarea
              className="mono"
              value={String(config.private_key ?? "")}
              onChange={(e) => onChange("private_key", e.target.value)}
              spellCheck={false}
              style={{
                width: "100%",
                minHeight: 110,
                resize: "vertical",
                background: "var(--as-bg-2)",
                color: "var(--as-ink)",
                border: "1px solid var(--as-line)",
                borderRadius: 8,
                padding: "8px 10px",
                fontSize: 12,
                fontFamily: "var(--as-mono)",
              }}
            />
          </FormField>
          <FormField label="Key passphrase" hint="leave blank if the key has none">
            <TextInput
              className="mono"
              type="password"
              value={String(config.private_key_passphrase ?? "")}
              onChange={(e) => onChange("private_key_passphrase", e.target.value)}
            />
          </FormField>
        </>
      ) : (
        <FormField label="Password">
          <TextInput
            className="mono"
            type="password"
            value={String(config.password ?? "")}
            onChange={(e) => onChange("password", e.target.value)}
          />
        </FormField>
      )}
      <FormField label="Remote path">
        <TextInput className="mono" value={String(config.remote_path ?? "/photos/{yyyy}/{mm}/{dd}/")} onChange={(e) => onChange("remote_path", e.target.value)} />
      </FormField>
    </>
  );
}
