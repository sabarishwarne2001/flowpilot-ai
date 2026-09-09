import React from "react";

import type {
  DestinationKind,
  WarehouseCredentialInput,
} from "@/types/analytics";

/**
 * ARCH-29 Slice 2 — one definition of what a warehouse credential looks like.
 *
 * WHY THIS MODULE EXISTS
 * ======================
 *
 * The destination editor needs the same per-kind credential fields the create
 * form already had. Copying them would create two definitions of the same
 * secret shape, and the failure mode is quiet: add a field to the S3
 * credential, update the create form, forget the editor, and rotation starts
 * silently writing an incomplete credential that only fails at the next
 * scheduled run — hours later, in a worker log, against a destination the
 * operator believes they just fixed.
 *
 * So the fields are data, declared once, and both surfaces render from them.
 *
 * ONE FORM PER KIND, STILL
 * ========================
 *
 * The original comment on the create form is worth preserving: the alternative
 * to switching on `kind` is one form with every field and a note saying which
 * ones apply, which is how a tenant pastes a BigQuery key into a Snowflake
 * destination and finds out at the first scheduled run.
 */

export type CredentialFieldType = "text" | "secret" | "area";

export interface CredentialFieldSpec {
  readonly key: string;
  readonly label: string;
  readonly hint?: string;
  readonly type: CredentialFieldType;
  /** Applied when the operator leaves the field blank. */
  readonly defaultValue?: string;
  /** Spans both columns in a two-column grid. */
  readonly wide?: boolean;
}

export const CREDENTIAL_FIELDS: Record<
  DestinationKind,
  readonly CredentialFieldSpec[]
> = {
  S3: [
    { key: "bucket", label: "Bucket", type: "text" },
    { key: "region", label: "Region", hint: "e.g. eu-west-1", type: "text" },
    {
      key: "prefix",
      label: "Key prefix",
      hint: "Defaults to flowpilot/",
      type: "text",
      defaultValue: "flowpilot/",
    },
    { key: "access_key_id", label: "Access key ID", type: "text" },
    { key: "secret_access_key", label: "Secret access key", type: "secret" },
  ],
  BIGQUERY: [
    { key: "project_id", label: "Project ID", type: "text" },
    { key: "dataset", label: "Dataset", type: "text" },
    {
      key: "location",
      label: "Location",
      hint: "Must match the dataset's own location, or the load is rejected after upload.",
      type: "text",
      defaultValue: "US",
    },
    {
      key: "service_account_json",
      label: "Service account JSON",
      hint: "Paste the whole key file. A user OAuth client will not work for unattended loads.",
      type: "area",
      wide: true,
    },
  ],
  DATABRICKS: [
    {
      key: "host",
      label: "Workspace host",
      hint: "Hostname only, no https://",
      type: "text",
    },
    { key: "warehouse_id", label: "SQL warehouse ID", type: "text" },
    {
      key: "catalog",
      label: "Catalog",
      hint: "Defaults to main",
      type: "text",
      defaultValue: "main",
    },
    {
      key: "db_schema",
      label: "Schema",
      hint: "Defaults to default",
      type: "text",
      defaultValue: "default",
    },
    {
      key: "volume",
      label: "Unity Catalog volume",
      hint: "Receives the Parquet before COPY INTO reads it.",
      type: "text",
    },
    { key: "access_token", label: "Personal access token", type: "secret" },
  ],
  SNOWFLAKE: [
    {
      key: "account",
      label: "Account identifier",
      hint: "e.g. xy12345.eu-west-1",
      type: "text",
    },
    { key: "user", label: "User", type: "text" },
    { key: "warehouse", label: "Warehouse", type: "text" },
    { key: "database", label: "Database", type: "text" },
    {
      key: "db_schema",
      label: "Schema",
      hint: "Defaults to PUBLIC",
      type: "text",
      defaultValue: "PUBLIC",
    },
    {
      key: "stage_name",
      label: "External stage",
      hint: "A stage you have already created, pointing at the bucket below.",
      type: "text",
    },
    { key: "stage_bucket", label: "Stage bucket", type: "text" },
    { key: "stage_region", label: "Stage region", type: "text" },
    { key: "stage_access_key_id", label: "Stage access key ID", type: "text" },
    {
      key: "stage_secret_access_key",
      label: "Stage secret access key",
      type: "secret",
    },
    {
      key: "private_key",
      label: "Private key (PKCS#8 PEM)",
      hint: "Snowflake's SQL API authenticates with a key pair; it has no password path. Paste the private half — the public half stays in Snowflake.",
      type: "area",
      wide: true,
    },
  ],
};

/** Resolves a field to its entered value, or its declared default when blank. */
const resolve = (
  spec: CredentialFieldSpec,
  value: (key: string) => string,
): string => value(spec.key) || spec.defaultValue || "";

/**
 * Builds the credential payload for a kind from a flat field bag.
 *
 * The cast is confined to this one place. Each branch lists exactly the keys
 * its union member declares, so a field added to CREDENTIAL_FIELDS without a
 * matching entry here is a field the operator can fill and the request will
 * never carry — hence the branches are written out rather than spread from
 * the spec.
 */
export const buildCredential = (
  kind: DestinationKind,
  value: (key: string) => string,
): WarehouseCredentialInput => {
  const specs = CREDENTIAL_FIELDS[kind];
  const at = (key: string): string => {
    const spec = specs.find((candidate) => candidate.key === key);
    return spec ? resolve(spec, value) : value(key);
  };

  switch (kind) {
    case "SNOWFLAKE":
      return {
        kind: "SNOWFLAKE",
        account: at("account"),
        user: at("user"),
        warehouse: at("warehouse"),
        database: at("database"),
        db_schema: at("db_schema"),
        stage_name: at("stage_name"),
        stage_bucket: at("stage_bucket"),
        stage_region: at("stage_region"),
        private_key: at("private_key"),
        stage_access_key_id: at("stage_access_key_id"),
        stage_secret_access_key: at("stage_secret_access_key"),
      };
    case "BIGQUERY":
      return {
        kind: "BIGQUERY",
        project_id: at("project_id"),
        dataset: at("dataset"),
        location: at("location"),
        service_account_json: at("service_account_json"),
      };
    case "DATABRICKS":
      return {
        kind: "DATABRICKS",
        host: at("host"),
        warehouse_id: at("warehouse_id"),
        catalog: at("catalog"),
        db_schema: at("db_schema"),
        volume: at("volume"),
        access_token: at("access_token"),
      };
    default:
      return {
        kind: "S3",
        bucket: at("bucket"),
        region: at("region"),
        prefix: at("prefix"),
        access_key_id: at("access_key_id"),
        secret_access_key: at("secret_access_key"),
      };
  }
};

/**
 * True when every field without a default has been filled.
 *
 * A credential is all-or-nothing on the wire: there is no partial rotation,
 * because merging a new secret into a stored one means the plaintext exists in
 * a request handler for a reason other than storing it. So a half-filled
 * rotation form must not be submittable.
 */
export const credentialIsComplete = (
  kind: DestinationKind,
  value: (key: string) => string,
): boolean =>
  CREDENTIAL_FIELDS[kind].every(
    (spec) => resolve(spec, value).trim().length > 0,
  );

const LABEL = "text-sm font-medium text-foreground";
const HINT = "mt-1 text-xs text-muted-foreground";
const INPUT =
  "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

export interface CredentialFieldsetProps {
  readonly kind: DestinationKind;
  readonly value: (key: string) => string;
  readonly onChange: (
    key: string,
  ) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => void;
  /** Disambiguates input ids when two field sets are on one page. */
  readonly idPrefix?: string;
}

export const CredentialFieldset: React.FC<CredentialFieldsetProps> = ({
  kind,
  value,
  onChange,
  idPrefix = "field",
}) => (
  <>
    {CREDENTIAL_FIELDS[kind].map((spec) => {
      const id = `${idPrefix}-${spec.key}`;
      const body =
        spec.type === "area" ? (
          <textarea
            id={id}
            className={`${INPUT} h-28 font-mono text-xs`}
            value={value(spec.key)}
            onChange={onChange(spec.key)}
          />
        ) : (
          <input
            id={id}
            className={INPUT}
            type={spec.type === "secret" ? "password" : "text"}
            value={value(spec.key)}
            onChange={onChange(spec.key)}
            autoComplete={spec.type === "secret" ? "new-password" : "off"}
          />
        );

      return (
        <div key={spec.key} className={spec.wide ? "md:col-span-2" : undefined}>
          <label className={LABEL} htmlFor={id}>
            {spec.label}
          </label>
          {body}
          {spec.hint ? <p className={HINT}>{spec.hint}</p> : null}
        </div>
      );
    })}
  </>
);
