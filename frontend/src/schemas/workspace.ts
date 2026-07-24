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

  timezone: z.enum([
    "Asia/Kolkata",
    "Asia/Dubai",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Asia/Singapore",
    "Australia/Sydney",
    "UTC",
  ]),

  language: z.enum([
    "en",
    "hi",
    "ta",
    "ml",
    "te",
    "kn",
    "ar",
    "de",
    "fr",
    "es",
    "ja",
    "zh",
  ]),

  currency: z.enum([
    "INR",
    "USD",
    "EUR",
    "GBP",
    "AED",
    "SGD",
    "AUD",
    "CAD",
    "JPY",
    "CNY",
  ]),

  date_format: z.enum([
    "DD-MM-YYYY",
    "MM-DD-YYYY",
    "YYYY-MM-DD",
    "DD/MM/YYYY",
    "MM/DD/YYYY",
    "YYYY/MM/DD",
  ]),

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
