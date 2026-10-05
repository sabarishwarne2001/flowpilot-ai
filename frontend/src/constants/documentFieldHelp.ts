export const DOCUMENT_FIELD_HELP = {
  chunk_size: {
    title: "Chunk Size",
    description:
      "Maximum number of characters in each text chunk before embeddings are generated.",
    recommended: "500 characters works well for most document types.",
  },

  chunk_overlap: {
    title: "Chunk Overlap",
    description:
      "Number of characters shared between consecutive chunks to preserve context.",
    recommended: "100 characters preserves context between chunks.",
  },

  embedding_model: {
    title: "Embedding Model",
    description:
      "Sentence Transformer model used to generate vector embeddings for semantic search.",
    recommended: "sentence-transformers/all-MiniLM-L6-v2",
  },

  ocr_language: {
    title: "OCR Language",
    description:
      "Default OCR language used when extracting text from scanned documents and images.",
    recommended: "eng",
  },

  max_upload_size: {
    title: "Maximum Upload Size",
    description:
      "Maximum allowed upload size for a single document in megabytes.",
    recommended: "50 MB",
  },

  allowed_file_types: {
    title: "Allowed File Types",
    description:
      "Comma-separated list of document extensions users are allowed to upload.",
    recommended: "pdf,png,jpg,jpeg",
  },

  duplicate_detection: {
    title: "Duplicate Detection",
    description:
      "Detect previously uploaded documents and prevent duplicate processing.",
    recommended: "Enabled",
  },

  automatic_classification: {
    title: "Automatic Classification",
    description:
      "Automatically classify uploaded documents into predefined categories.",
    recommended: "Enabled",
  },

  automatic_summarization: {
    title: "Automatic Summarization",
    description:
      "Generate AI summaries immediately after document processing.",
    recommended: "Enabled",
  },

  automatic_entity_extraction: {
    title: "Automatic Entity Extraction",
    description:
      "Extract fields such as vendor, invoice number, dates, amounts and line items during processing. Three-way matching, ERP posting, the entity graph, cases, the audit radar and obligations all read these fields; with this off they receive nothing.",
    recommended: "Enabled",
  },

  verification_enabled: {
    title: "Multi-agent Verification",
    description:
      "Several independent AI readings of each document are compared field by field. Fields they disagree on go to the review queue, and reviewers' corrections teach extraction memory. Each agent is one more AI call per document.",
    recommended: "Enabled for invoices and contracts you post or pay",
  },

  verification_agents: {
    title: "Verification Agents",
    description:
      "How many independent readings to compare (2 to 5). More agents catch more errors and cost more.",
    recommended: "3",
  },
} as const;
