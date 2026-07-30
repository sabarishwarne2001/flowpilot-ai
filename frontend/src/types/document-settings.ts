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
}

export type UpdateDocumentSettings =
  Omit<
    DocumentSettings,
    "id" | "user_id" | "created_at" | "updated_at"
  >;
