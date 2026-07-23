import { z } from "zod";

export const workspaceSchema = z.object({
  workspace_name: z
    .string()
    .trim()
    .min(1, "Workspace name is required.")
    .max(100, "Workspace name cannot exceed 100 characters."),

  company_name: z
    .string()
    .trim()
    .min(1, "Company name is required.")
    .max(150, "Company name cannot exceed 150 characters."),

  company_logo_url: z
    .string()
    .trim()
    .url("Please enter a valid URL.")
    .nullable()
    .or(z.literal("")),

  timezone: z
    .string()
    .trim()
    .min(1, "Timezone is required."),

  language: z
    .string()
    .trim()
    .min(1, "Language is required."),

  currency: z
    .string()
    .trim()
    .min(1, "Currency is required."),

  date_format: z
    .string()
    .trim()
    .min(1, "Date format is required."),

  primary_color: z
    .string()
    .trim()
    .min(1),

  secondary_color: z
    .string()
    .trim()
    .min(1),

  is_active: z.boolean(),
});

export type WorkspaceFormData = z.infer<typeof workspaceSchema>;
