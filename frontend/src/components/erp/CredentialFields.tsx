/**
 * ARCH47-S2:credential-fields — the fields one sign-in method takes. Secrets
 * are password inputs, never echoed, and are sent once to be stored encrypted.
 */
import React from "react";

import { FIELD_LABEL, HINT, INPUT, TEXTAREA } from "@/components/ui/primitives";
import { CREDENTIAL_FIELDS, SECRET_FIELDS, type AuthMode } from "@/types/erp";

const LABELS: Readonly<Record<string, string>> = {
  token: "Token",
  username: "Username",
  password: "Password",
  value: "API key",
  header: "Header name (default X-API-Key)",
  client_id: "Client ID",
  client_secret: "Client secret",
  refresh_token: "Refresh token",
  private_key: "Private key (OpenSSH or PEM)",
  passphrase: "Key passphrase",
};

interface CredentialFieldsProps {
  readonly authMode: AuthMode;
  readonly value: Readonly<Record<string, string>>;
  readonly onChange: (value: Record<string, string>) => void;
}

export const CredentialFields: React.FC<CredentialFieldsProps> = ({ authMode, value, onChange }) => {
  const spec = CREDENTIAL_FIELDS[authMode];
  const fields = [...spec.required, ...spec.optional];
  if (fields.length === 0) {
    return <p className={HINT}>No credential: the file is kept here for a person to import.</p>;
  }
  const set = (key: string, next: string): void => {
    const out = { ...value };
    if (next) {
      out[key] = next;
    } else {
      delete out[key];
    }
    onChange(out);
  };
  return (
    <fieldset className="space-y-2">
      <legend className={FIELD_LABEL}>Credential</legend>
      <p className={HINT}>Stored encrypted. It is never shown again — only its fingerprint.</p>
      <div className="grid gap-3 md:grid-cols-2">
        {fields.map((key) => (
          <label key={key} className={`space-y-1 ${key === "private_key" ? "md:col-span-2" : ""}`}>
            <span className="text-xs font-medium">
              {LABELS[key] ?? key}
              {spec.required.includes(key) ? <span className="text-destructive"> *</span> : null}
            </span>
            {key === "private_key" ? (
              <textarea className={`${TEXTAREA} font-mono text-xs`} value={value[key] ?? ""} spellCheck={false} autoComplete="off"
                onChange={(e) => set(key, e.target.value)} />
            ) : (
              <input className={INPUT} type={SECRET_FIELDS.has(key) ? "password" : "text"} autoComplete="off"
                value={value[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
            )}
          </label>
        ))}
      </div>
    </fieldset>
  );
};

export default CredentialFields;
