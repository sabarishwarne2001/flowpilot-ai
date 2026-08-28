/** Mirrors app/core/scopes.py::ApiKeyScope. */
export const API_KEY_SCOPES = [
  "organizations:read",
  "workspaces:read",
  "workspaces:write",
  "members:read",
  "work_items:read",
  "work_items:write",
  "audit_logs:read",
  "files:read",
  "files:write",
  "webhooks:read",
  "webhooks:write",
  "webhooks:admin",
  "billing:read",
] as const;

export type ApiKeyScope = (typeof API_KEY_SCOPES)[number];

export interface ApiKeyRead {
  readonly id: string;
  readonly organization_id: string;
  readonly user_id: string | null;
  readonly name: string;
  readonly scopes: readonly string[];
  readonly last_used_at: string | null;
  readonly expires_at: string | null;
  readonly deactivated_at: string | null;
  readonly deactivated_reason: string | null;
  readonly previous_secret_expires_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ApiKeyResponse {
  readonly api_key: ApiKeyRead;
  readonly token: string;
}

export interface ApiKeyCreateRequest {
  readonly name: string;
  readonly scopes: readonly ApiKeyScope[];
  readonly expires_at?: string | null;
}
