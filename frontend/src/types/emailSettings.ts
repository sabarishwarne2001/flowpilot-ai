/**
 * ARCH40-S2:email-types. The workspace email override and its resolution.
 *
 * The override owns workspace-level email from ARCH-40 on
 * (`workspace_email_overrides`); `email_settings` keeps its rows and has no
 * reader. The password is never returned — `has_password` says whether one
 * is stored, and a save that omits it keeps the stored one.
 */

export type EmailEncryption = "NONE" | "TLS" | "SSL";

export interface WorkspaceEmailOverride {
  readonly workspace_id: string;
  readonly organization_id: string;
  readonly is_enabled: boolean;
  readonly smtp_host: string | null;
  readonly smtp_port: number | null;
  readonly smtp_username: string | null;
  readonly sender_name: string | null;
  readonly encryption: EmailEncryption;
  readonly from_address: string | null;
  readonly reply_to_address: string | null;
  readonly has_password: boolean;
  readonly updated_by_user_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface WorkspaceEmailOverrideUpdate {
  is_enabled: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_username: string | null;
  /** Omitted or null keeps the stored password. */
  smtp_password?: string | null;
  sender_name: string | null;
  encryption: EmailEncryption;
  from_address: string | null;
  reply_to_address: string | null;
}

export type EmailLayer =
  | "WORKSPACE_OVERRIDE"
  | "BRANDING_SENDER"
  | "ORGANIZATION"
  | "PLATFORM";

export interface EmailResolutionLayer {
  readonly layer: EmailLayer | string;
  readonly applied: boolean;
  readonly reason: string | null;
  readonly detail: string | null;
}

export interface EmailResolution {
  readonly from_address: string;
  readonly sender_name: string;
  readonly reply_to: string | null;
  readonly template_namespace: string;
  readonly transport_layer: EmailLayer | string;
  readonly identity_layer: EmailLayer | string;
  readonly smtp_host: string;
  readonly degraded_reason: string | null;
  readonly trail: readonly EmailResolutionLayer[];
}

export interface TestEmailRequest {
  recipient: string;
}

export interface TestEmailResponse {
  success: boolean;
  message: string;
  transport_layer?: string | null;
  from_address?: string | null;
}
