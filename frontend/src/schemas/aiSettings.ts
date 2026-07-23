import { z } from "zod";

export const aiSettingsSchema = z.object({
    provider: z.enum([
        "GROQ",
        "GEMINI",
        "OPENAI",
        "CLAUDE",
    ]),

    model: z
        .string()
        .min(1, "Model is required."),

    temperature: z
        .number()
        .min(0)
        .max(2),

    max_output_tokens: z
        .number()
        .int()
        .min(1),

    top_p: z
        .number()
        .min(0)
        .max(1),

    frequency_penalty: z
        .number()
        .min(0)
        .max(2),

    presence_penalty: z
        .number()
        .min(0)
        .max(2),

    input_cost_per_1k_tokens: z
        .number()
        .min(0),

    output_cost_per_1k_tokens: z
        .number()
        .min(0),

    system_prompt_version: z
        .string()
        .min(1),

    prompt_version: z
        .string()
        .min(1),

    enable_token_tracking: z.boolean(),

    enable_streaming: z.boolean(),
});

export type AISettingsFormData = z.infer<
    typeof aiSettingsSchema
>;
