export interface Workspace {
  id: string;
  user_id: string;

  workspace_name: string;
  company_name: string;
  company_logo_url: string | null;

  timezone:
    | "Asia/Kolkata"
    | "Asia/Dubai"
    | "Europe/London"
    | "Europe/Berlin"
    | "America/New_York"
    | "America/Chicago"
    | "America/Los_Angeles"
    | "Asia/Singapore"
    | "Australia/Sydney"
    | "UTC";

  language:
    | "en"
    | "hi"
    | "ta"
    | "ml"
    | "te"
    | "kn"
    | "ar"
    | "de"
    | "fr"
    | "es"
    | "ja"
    | "zh";

  currency:
    | "INR"
    | "USD"
    | "EUR"
    | "GBP"
    | "AED"
    | "SGD"
    | "AUD"
    | "CAD"
    | "JPY"
    | "CNY";

  date_format:
    | "DD-MM-YYYY"
    | "MM-DD-YYYY"
    | "YYYY-MM-DD"
    | "DD/MM/YYYY"
    | "MM/DD/YYYY"
    | "YYYY/MM/DD";

  primary_color: string;
  secondary_color: string;

  is_active: boolean;

  created_at: string;
  updated_at: string;
}

export type WorkspaceCreate = Omit<
  Workspace,
  "id" | "user_id" | "created_at" | "updated_at"
>;
