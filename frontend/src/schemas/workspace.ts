import { z } from "zod";

/**
 * Workspace settings form validation.
 *
 * ARCH-01 changed two fields.
 *
 * company_name left the workspace model. It was the tenant's identity, not the
 * workspace's, and moved to Organization.name. It remains in this schema
 * because the settings form still presents it — but it submits to the
 * organization endpoint, not the workspace one.
 *
 * is_active was replaced by the WorkspaceStatus enum, which distinguishes
 * archived from suspended where a boolean could not. Archiving is now an
 * action with server-side guards rather than a form field, so it has left this
 * schema entirely.
 *
 * Maximum lengths mirror app/models/workspace.py exactly, including the wider
 * language bound: BCP-47 tags such as "sr-Latn-RS-u-ca-gregory" exceed ten
 * characters.
 */
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
});

export type WorkspaceFormData = z.infer<typeof workspaceSchema>;
