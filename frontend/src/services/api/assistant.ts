import apiClient from "@/services/api/client";
import type {
  ConversationSummary,
  ConversationHistoryResponse,
  ChatQueryRequest,
  ChatResponse,
  ConversationCreateRequest,
  ConversationUpdateRequest,
  ConversationHistoryQuery,
} from "@/types/assistant";

/**
 * ============================================================================
 * Assistant API Constants
 * ============================================================================
 */

const ASSISTANT_ENDPOINTS = {
  CONVERSATIONS: "/assistant/conversations",
  CONVERSATION: (conversationId: string) =>
    `/assistant/conversations/${conversationId}`,
  MESSAGES: (conversationId: string) =>
    `/assistant/conversations/${conversationId}/messages`,
} as const;

const JSON_HEADERS = {
  Accept: "application/json",
} as const;

const DEFAULT_HISTORY_PAGE_SIZE = 50;

/**
 * ============================================================================
 * Conversation APIs
 * ============================================================================
 */

/**
 * Creates a new conversation.
 *
 * The backend automatically generates the conversation
 * title after receiving the first user message.
 */
export const createConversation = async (
  workItemId?: string | null
): Promise<ConversationSummary> => {
  const payload: ConversationCreateRequest = {
    work_item_id: workItemId ?? null,
  };

  const response = await apiClient.post<ConversationSummary>(
    ASSISTANT_ENDPOINTS.CONVERSATIONS,
    payload,
    {
      headers: JSON_HEADERS,
    }
  );

  return response.data;
};

/**
 * Retrieves every active conversation owned by
 * the authenticated user.
 */
export const getConversations = async (): Promise<
  readonly ConversationSummary[]
> => {
  const response = await apiClient.get<readonly ConversationSummary[]>(
    ASSISTANT_ENDPOINTS.CONVERSATIONS,
    {
      headers: JSON_HEADERS,
    }
  );

  return response.data;
};

/**
 * ============================================================================
 * Conversation History
 * ============================================================================
 */

/**
 * Retrieves paginated conversation history.
 *
 * Designed to support future infinite scrolling.
 */
export const getConversationHistory = async (
  conversationId: string,
  query: ConversationHistoryQuery = {}
): Promise<ConversationHistoryResponse> => {
  const { limit = DEFAULT_HISTORY_PAGE_SIZE, cursor } = query;

  const queryParams = new URLSearchParams();

  queryParams.append("limit", limit.toString());

  if (cursor) {
    queryParams.append("cursor", cursor);
  }

  const response = await apiClient.get<ConversationHistoryResponse>(
    ASSISTANT_ENDPOINTS.CONVERSATION(conversationId),
    {
      params: queryParams,
      headers: JSON_HEADERS,
    }
  );

  return response.data;
};

/**
 * ============================================================================
 * Messaging APIs
 * ============================================================================
 */

/**
 * Sends a user message to the assistant.
 *
 * NOTE:
 * The current implementation uses a traditional
 * request/response workflow.
 *
 * TODO (Future):
 * Upgrade to Server Sent Events (SSE) or
 * streaming responses without changing the
 * public API contract.
 */
export const sendChatMessage = async (
  conversationId: string,
  content: string,
  options?: {
    stream?: boolean;
    signal?: AbortSignal;
  }
): Promise<ChatResponse> => {
  const trimmedContent = content.trim();

  if (!trimmedContent) {
    throw new Error("Message content cannot be empty.");
  }

  const payload: ChatQueryRequest = {
    content: trimmedContent,
  };

  const requestConfig = {
    headers: JSON_HEADERS,
    ...(options?.signal
      ? {
          signal: options.signal,
        }
      : {}),
  };

  const response = await apiClient.post<ChatResponse>(
    ASSISTANT_ENDPOINTS.MESSAGES(conversationId),
    payload,
    requestConfig
  );

  return response.data;
};

/**
 * ============================================================================
 * Conversation Management
 * ============================================================================
 */

/**
 * Renames an existing conversation.
 */
export const renameConversation = async (
  conversationId: string,
  title: string
): Promise<ConversationSummary> => {
  const trimmedTitle = title.trim();

  const payload: ConversationUpdateRequest = {
    title: trimmedTitle,
  };

  const response = await apiClient.patch<ConversationSummary>(
    ASSISTANT_ENDPOINTS.CONVERSATION(conversationId),
    payload,
    {
      headers: JSON_HEADERS,
    }
  );

  return response.data;
};

/**
 * Deletes a conversation together with all
 * associated messages.
 */
export const deleteConversation = async (
  conversationId: string
): Promise<void> => {
  await apiClient.delete(ASSISTANT_ENDPOINTS.CONVERSATION(conversationId), {
    headers: JSON_HEADERS,
  });
};

/**
 * ============================================================================
 * Unified Assistant API
 * ============================================================================
 */

export const assistantApi = {
  createConversation,
  getConversations,
  getConversationHistory,
  sendChatMessage,
  renameConversation,
  deleteConversation,
};

export default assistantApi;
