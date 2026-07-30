import { z } from "zod";

export const documentSettingsSchema = z.object({
    chunk_size: z
        .number()
        .min(100)
        .max(4000),

    chunk_overlap: z
        .number()
        .min(0)
        .max(1000),

    embedding_model: z
        .string()
        .min(1)
        .max(100),

    ocr_language: z
        .string()
        .min(1)
        .max(20),

    max_upload_size: z
        .number()
        .min(1)
        .max(500),

    allowed_file_types: z
        .string()
        .min(1)
        .max(255),

    duplicate_detection: z.boolean(),

    automatic_classification: z.boolean(),

    automatic_summarization: z.boolean(),

    automatic_entity_extraction: z.boolean(),
});

export type DocumentSettingsFormData =
    z.infer<typeof documentSettingsSchema>;
