export interface Workspace {
  id: string;
  user_id: string;

  workspace_name: string;
  company_name: string;
  company_logo_url: string | null;

  timezone: string;
  language: string;
  currency: string;
  date_format: string;

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
