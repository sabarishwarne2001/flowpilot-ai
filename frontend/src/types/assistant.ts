/**
 * AI Assistant and Conversational Memory Data Transfer Objects (DTOs)
 * for FlowPilot AI.
 *
 * Defines immutable TypeScript contracts shared between the frontend
 * and backend while preparing the application for streaming responses,
 * conversation pagination, telemetry collection, and future multi-agent
 * orchestration.
 */

/* ========================================================================== */
/* Common Types                                                               */
/* ========================================================================== */

/**
 * Supported conversation participant roles.
 */
export type ConversationRole =
  | "user"
  | "assistant";

/**
 * Reserved agent categories for future multi-agent workflows.
 */
export type AgentType =
  | "assistant"
  | "rag"
  | "workflow"
  | "classification"
  | "extraction"
  | "system";

/**
 * Supported model identifiers.
 *
 * Extend this union whenever additional providers/models are introduced.
 */
export type AIModelIdentifier =
  | "gpt-5"
  | "gpt-4.1"
  | "claude"
  | "gemini"
  | "llama"
  | "mistral"
  | "custom";

/**
 * Conversation lifecycle state.
 */
export type ConversationStatus =
  | "ACTIVE"
  | "ARCHIVED"
  | "DELETED";

/* ========================================================================== */
/* Citation Models                                                            */
/* ========================================================================== */

/**
 * Metadata describing a document chunk referenced during RAG generation.
 */
export interface SourceCitation {
  /**
   * Stable unique identifier.
   * React rendering should never depend on array indexes.
   */
  readonly citation_id: string;

  readonly work_item_id: string;

  /**
   * Original uploaded filename.
   */
  readonly original_filename: string;

  /**
   * Optional UI-friendly display name.
   * Allows future renaming without affecting storage.
   */
  readonly document_display_name?: string;

  readonly chunk_index: number;

  readonly similarity_score: number;

  readonly page_number: number | null;

  readonly snippet: string;
}

/* ========================================================================== */
/* Message Models                                                             */
/* ========================================================================== */

/**
 * Single chronological message belonging to a conversation.
 */
export interface ConversationMessage {
  readonly id: string;

  readonly conversation_id: string;

  /**
   * Guarantees deterministic ordering during streaming.
   */
  readonly sequence_number: number;

  readonly role: ConversationRole;

  readonly content: string;

  readonly sources: readonly SourceCitation[];

  readonly created_at: string;

  readonly updated_at: string;

  /* ---------------------------------------------------------------------- */
  /* Future Multi-Agent Metadata                                             */
  /* ---------------------------------------------------------------------- */

  readonly agent_name?: string;

  readonly agent_type?: AgentType;

  readonly model?: AIModelIdentifier;

  readonly token_usage?: TokenUsage;
}

/* ========================================================================== */
/* Conversation Models                                                        */
/* ========================================================================== */

/**
 * Lightweight conversation metadata.
 *
 * Used when listing conversations without loading
 * the complete message history.
 */
export interface ConversationSummary {
  readonly id: string;

  readonly user_id: string;

  readonly title: string;

  readonly work_item_id: string | null;

  readonly status: ConversationStatus;

  readonly created_at: string;

  readonly updated_at: string;
}

/**
 * Cursor-paginated message history.
 *
 * Designed for future infinite scrolling.
 */
export interface ConversationHistoryResponse {
  readonly messages: readonly ConversationMessage[];

  readonly total_messages: number;

  readonly has_more: boolean;

  readonly next_cursor: string | null;
}

/* ========================================================================== */
/* Request DTOs                                                               */
/* ========================================================================== */

/**
 * User chat request payload.
 */
export interface ChatQueryRequest {
  readonly content: string;
}

/**
 * Create conversation request.
 *
 * Title generation is delegated to the backend
 * using the user's first message.
 */
export interface ConversationCreateRequest {
  readonly work_item_id?: string | null;
}

/**
 * Manual conversation rename request.
 */
export interface ConversationUpdateRequest {
  readonly title: string;
}

export interface ConversationHistoryQuery {
  readonly limit?: number;
  readonly cursor?: string | null;
}

/* ========================================================================== */
/* Telemetry                                                                  */
/* ========================================================================== */

/**
 * Token accounting returned by the LLM provider.
 */
export interface TokenUsage {
  readonly provider: string;

  readonly model: string;

  readonly prompt_tokens: number;

  readonly completion_tokens: number;

  readonly total_tokens: number;

  readonly estimated_cost: number;
}

/**
 * Optional performance telemetry.
 *
 * Designed for future analytics dashboards.
 */
export interface ChatTelemetry {
  readonly usage?: TokenUsage;

  readonly latency_ms?: number;

  readonly model?: AIModelIdentifier;
}

/* ========================================================================== */
/* Response DTOs                                                              */
/* ========================================================================== */

/**
 * Assistant response returned after a chat request.
 *
 * Compatible with traditional request/response APIs
 * and future streaming implementations.
 */
export interface ChatResponse {
  /**
   * Conversation identifier.
   *
   * Useful when the backend automatically creates
   * a new conversation.
   */
  readonly conversation_id: string;

  /**
   * Assistant message text.
   */
  readonly response: string;

  /**
   * Referenced document chunks.
   */
  readonly sources: readonly SourceCitation[];

  /**
   * Indicates whether the streamed response
   * has completed.
   *
   * Optional for non-streaming APIs.
   */
  readonly is_complete?: boolean;

  /**
   * Optional performance metrics.
   */
  readonly telemetry?: ChatTelemetry;
}
