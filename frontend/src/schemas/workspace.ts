import { z } from "zod";

import { isCurrencyCode } from "@/utils/currencies";
import { isUsableTimeZone } from "@/utils/displayTime";

export const workspaceSchema = z.object({
  workspace_name: z
    .string()
    .trim()
    .min(1, "Workspace name is required.")
    .max(100, "Workspace name cannot exceed 100 characters."),

  company_logo_url: z
    .string()
    .trim()
    .nullable()
    .or(z.literal("")),

  // F-321: any zone and currency the server accepts (F-320), not a short list of each. A
  // workspace holding a value outside a short list could not save this page at all.
  timezone: z
    .string()
    .trim()
    .refine(isUsableTimeZone, "Choose a timezone from the list (IANA name, e.g. Asia/Kolkata)."),

  // Sent back unchanged: language is each person's own setting (profile), kept for the API.
  language: z.string().trim().min(2),

  currency: z
    .string()
    .trim()
    .refine(isCurrencyCode, "Choose a currency from the list."),

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
