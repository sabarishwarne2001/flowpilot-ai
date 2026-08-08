import apiClient from "@/services/api/client";
import { ASSISTANT_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ConversationSummary,
  ConversationHistoryResponse,
  ChatQueryRequest,
  ChatResponse,
  ConversationCreateRequest,
  ConversationUpdateRequest,
  ConversationHistoryQuery,
} from "@/types/assistant";

const JSON_HEADERS = { Accept: "application/json" } as const;
const DEFAULT_HISTORY_PAGE_SIZE = 50;

export const createConversation = async (
  workspaceId: string,
  workItemId?: string | null,
): Promise<ConversationSummary> => {
  const payload: ConversationCreateRequest = { work_item_id: workItemId ?? null };
  const response = await apiClient.post<ConversationSummary>(
    ASSISTANT_ENDPOINTS.conversations(workspaceId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getDocumentConversation = async (
  workspaceId: string,
  workItemId: string,
): Promise<ConversationSummary> => {
  const response = await apiClient.get<ConversationSummary>(
    ASSISTANT_ENDPOINTS.documentConversation(workspaceId, workItemId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getConversations = async (
  workspaceId: string,
): Promise<readonly ConversationSummary[]> => {
  const response = await apiClient.get<readonly ConversationSummary[]>(
    ASSISTANT_ENDPOINTS.conversations(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getConversationHistory = async (
  workspaceId: string,
  conversationId: string,
  query: ConversationHistoryQuery = {},
): Promise<ConversationHistoryResponse> => {
  const { limit = DEFAULT_HISTORY_PAGE_SIZE, cursor } = query;
  const queryParams = new URLSearchParams();
  queryParams.append("limit", limit.toString());
  if (cursor) queryParams.append("cursor", cursor);

  const response = await apiClient.get<ConversationHistoryResponse>(
    ASSISTANT_ENDPOINTS.conversation(workspaceId, conversationId),
    { params: queryParams, headers: JSON_HEADERS },
  );
  return response.data;
};

export const sendChatMessage = async (
  workspaceId: string,
  conversationId: string,
  content: string,
  options?: { stream?: boolean; signal?: AbortSignal },
): Promise<ChatResponse> => {
  const trimmedContent = content.trim();
  if (!trimmedContent) throw new Error("Message content cannot be empty.");

  const payload: ChatQueryRequest = { content: trimmedContent };
  const response = await apiClient.post<ChatResponse>(
    ASSISTANT_ENDPOINTS.messages(workspaceId, conversationId),
    payload,
    { headers: JSON_HEADERS, ...(options?.signal ? { signal: options.signal } : {}) },
  );
  return response.data;
};

export const renameConversation = async (
  workspaceId: string,
  conversationId: string,
  title: string,
): Promise<ConversationSummary> => {
  const payload: ConversationUpdateRequest = { title: title.trim() };
  const response = await apiClient.patch<ConversationSummary>(
    ASSISTANT_ENDPOINTS.conversation(workspaceId, conversationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteConversation = async (
  workspaceId: string,
  conversationId: string,
): Promise<void> => {
  await apiClient.delete(
    ASSISTANT_ENDPOINTS.conversation(workspaceId, conversationId),
    { headers: JSON_HEADERS },
  );
};

export const assistantApi = {
  createConversation,
  getDocumentConversation,
  getConversations,
  getConversationHistory,
  sendChatMessage,
  renameConversation,
  deleteConversation,
};

export default assistantApi;
