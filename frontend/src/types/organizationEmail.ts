export type EmailEncryption = "NONE" | "TLS" | "SSL";

export interface OrganizationEmailSettings {
  readonly id: string;
  readonly organization_id: string;
  readonly smtp_host: string | null;
  readonly smtp_port: number | null;
  readonly smtp_username: string | null;
  readonly sender_name: string | null;
  readonly sender_email: string | null;
  readonly encryption: EmailEncryption | null;
  readonly is_enabled: boolean;
  readonly has_password: boolean;
}

export interface OrganizationEmailSettingsUpdate {
  readonly smtp_host?: string | undefined;
  readonly smtp_port?: number | undefined;
  readonly smtp_username?: string | undefined;
  readonly smtp_password?: string | undefined;
  readonly sender_name?: string | undefined;
  readonly sender_email?: string | undefined;
  readonly encryption?: EmailEncryption | undefined;
  readonly is_enabled?: boolean | undefined;
}

export interface OrganizationEmailTestResult {
  readonly success: boolean;
  readonly message: string;
}
