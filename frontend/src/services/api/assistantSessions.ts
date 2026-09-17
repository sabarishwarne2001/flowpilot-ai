/**
 * ARCH-39 — API client for conversation sessions.
 *
 * `listSessions` replaces `getConversations` in the Assistant sidebar. The
 * ARCH-11 list returned every message of every conversation; this one returns
 * a summary row per conversation.
 */

import { apiClient } from "@/services/api/client";
import { ASSISTANT_SESSION_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AssistantModelOption,
  ConversationScope,
  ConversationScopeUpdate,
  ConversationSession,
  ConversationSessionFilters,
  ConversationSessionUpdate,
  ExportFormat,
  PromptTemplate,
  PromptTemplateWrite,
} from "@/types/assistantSuite";

export const listSessions = async (
  workspaceId: string,
  filters: ConversationSessionFilters,
): Promise<ConversationSession[]> => {
  const params: Record<string, string | number | boolean> = {
    kind: filters.kind,
    archived: filters.archived,
    limit: 100,
  };
  const q = filters.q.trim();
  if (q) {
    params["q"] = q;
  }
  const { data } = await apiClient.get<ConversationSession[]>(
    ASSISTANT_SESSION_ENDPOINTS.sessions(workspaceId),
    { params },
  );
  return data;
};

export const updateSession = async (
  workspaceId: string,
  conversationId: string,
  payload: ConversationSessionUpdate,
): Promise<ConversationSession> => {
  const { data } = await apiClient.patch<ConversationSession>(
    ASSISTANT_SESSION_ENDPOINTS.session(workspaceId, conversationId),
    payload,
  );
  return data;
};

export const getScope = async (
  workspaceId: string,
  conversationId: string,
): Promise<ConversationScope> => {
  const { data } = await apiClient.get<ConversationScope>(
    ASSISTANT_SESSION_ENDPOINTS.scope(workspaceId, conversationId),
  );
  return data;
};

export const setScope = async (
  workspaceId: string,
  conversationId: string,
  payload: ConversationScopeUpdate,
): Promise<ConversationScope> => {
  const { data } = await apiClient.put<ConversationScope>(
    ASSISTANT_SESSION_ENDPOINTS.scope(workspaceId, conversationId),
    payload,
  );
  return data;
};

export const listModels = async (workspaceId: string): Promise<AssistantModelOption[]> => {
  const { data } = await apiClient.get<AssistantModelOption[]>(
    ASSISTANT_SESSION_ENDPOINTS.models(workspaceId),
  );
  return data;
};

export const listPromptTemplates = async (workspaceId: string): Promise<PromptTemplate[]> => {
  const { data } = await apiClient.get<PromptTemplate[]>(
    ASSISTANT_SESSION_ENDPOINTS.templates(workspaceId),
  );
  return data;
};

export const createPromptTemplate = async (
  workspaceId: string,
  payload: PromptTemplateWrite,
): Promise<PromptTemplate> => {
  const { data } = await apiClient.post<PromptTemplate>(
    ASSISTANT_SESSION_ENDPOINTS.templates(workspaceId),
    payload,
  );
  return data;
};

export const archivePromptTemplate = async (
  workspaceId: string,
  templateId: string,
): Promise<void> => {
  await apiClient.delete(ASSISTANT_SESSION_ENDPOINTS.template(workspaceId, templateId));
};

/**
 * Downloads the export through the authenticated client, then hands the
 * browser a blob URL. A plain link would arrive without the bearer token.
 */
export const downloadSessionExport = async (
  workspaceId: string,
  conversationId: string,
  format: ExportFormat,
): Promise<void> => {
  const response = await apiClient.get<Blob>(
    ASSISTANT_SESSION_ENDPOINTS.exportSession(workspaceId, conversationId),
    { params: { format }, responseType: "blob" },
  );
  const disposition = String(response.headers["content-disposition"] ?? "");
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `conversation.${format === "json" ? "json" : "md"}`;
  const url = URL.createObjectURL(response.data);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }
};

export const assistantSessionsApi = {
  listSessions,
  updateSession,
  getScope,
  setScope,
  listModels,
  listPromptTemplates,
  createPromptTemplate,
  archivePromptTemplate,
  downloadSessionExport,
};
