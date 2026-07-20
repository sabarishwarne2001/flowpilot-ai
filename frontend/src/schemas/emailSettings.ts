import { z } from "zod";

export const emailSettingsSchema = z.object({
  sender_name: z
    .string()
    .trim()
    .min(1, "Sender name is required.")
    .max(100, "Sender name cannot exceed 100 characters."),

  smtp_host: z
    .string()
    .trim()
    .min(1, "SMTP host is required."),

  smtp_port: z
    .number()
    .int("SMTP port must be a whole number.")
    .min(1, "SMTP port must be greater than 0.")
    .max(65535, "SMTP port cannot exceed 65535."),

  smtp_username: z
    .string()
    .trim()
    .email("Please enter a valid email address."),

  smtp_password: z
    .string()
    .min(1, "SMTP password is required."),

  encryption: z.enum(["NONE", "TLS", "SSL"]),
});

export type EmailSettingsFormData = z.infer<typeof emailSettingsSchema>;
