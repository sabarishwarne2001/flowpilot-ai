export interface EmailSettings {
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  sender_name: string;
  encryption: "NONE" | "TLS" | "SSL";
}

export interface EmailSettingsCreate {
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  sender_name: string;
  encryption: "NONE" | "TLS" | "SSL";
}

export interface TestEmailRequest {
  recipient: string;
}

export interface TestEmailResponse {
  success: boolean;
  message: string;
}
