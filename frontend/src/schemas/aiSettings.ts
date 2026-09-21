import { z } from "zod";

/**
 * ARCH40-S2:ai-settings-schema. The same ranges the database now enforces
 * (ck_ai_settings_*_range) and the backend schema states. Before ARCH-40 this
 * was the only check anywhere, and the penalties were narrower here (0..2)
 * than the providers accept (-2..2). `verify_arch40.py` gate F1 compares
 * these keys with the backend's AISettingsBase.
 */
export const aiSettingsSchema = z.object({
    provider: z.enum([
        "GROQ",
        "GEMINI",
    ]),

    model: z
        .string()
        .trim()
        .min(1, "Model is required.")
        .max(100),

    temperature: z
        .number()
        .min(0)
        .max(2),

    max_output_tokens: z
        .number()
        .int()
        .min(1)
        .max(32768),

    top_p: z
        .number()
        .min(0)
        .max(1),

    frequency_penalty: z
        .number()
        .min(-2)
        .max(2),

    presence_penalty: z
        .number()
        .min(-2)
        .max(2),

    enable_streaming: z.boolean(),
});

export type AISettingsFormData = z.infer<
    typeof aiSettingsSchema
>;
