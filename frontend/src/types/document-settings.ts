export interface DocumentSettings {
  id: string;
  user_id: string;

  chunk_size: number;
  chunk_overlap: number;

  embedding_model: string;

  ocr_language: string;

  max_upload_size: number;

  allowed_file_types: string;

  duplicate_detection: boolean;

  automatic_classification: boolean;

  automatic_summarization: boolean;

  automatic_entity_extraction: boolean;

  created_at: string;
  updated_at: string;
  /** HARDENING-T2:D13 — platform facts the pipeline actually uses (read-only). */
  readonly platform_embedding_model?: string;
  readonly platform_embedding_dimension?: number;
  readonly platform_ocr_language?: string;
  /** Extensions the platform accepts; the workspace may narrow, never widen. */
  readonly supported_file_types?: string[];
}

export type UpdateDocumentSettings =
  Omit<
    DocumentSettings,
    "id" | "user_id" | "created_at" | "updated_at"
  >;
