/**
 * Query key factories. Every tenant-scoped key begins ["ws", workspaceId].
 */

import type { QueryClient, Query } from "@tanstack/react-query";
import type { WorkItemQueryFilters } from "@/types/workItem";

export const workspaceScope = (workspaceId: string) =>
  ["ws", workspaceId] as const;

export const workItemKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "work-items"] as const,
  list: (workspaceId: string, filters: WorkItemQueryFilters) =>
    [...workItemKeys.all(workspaceId), "list", filters] as const,
  detail: (workspaceId: string, workItemId: string) =>
    [...workItemKeys.all(workspaceId), "detail", workItemId] as const,
};

export const dashboardKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "dashboard"] as const,
  overview: (workspaceId: string) => [...dashboardKeys.all(workspaceId), "overview"] as const,
};

export const assistantKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "assistant"] as const,
  conversations: (workspaceId: string) =>
    [...assistantKeys.all(workspaceId), "conversations"] as const,
  history: (workspaceId: string, conversationId: string) =>
    [...assistantKeys.all(workspaceId), "history", conversationId] as const,
  documentConversation: (workspaceId: string, workItemId: string) =>
    [...assistantKeys.all(workspaceId), "document", workItemId] as const,
};

export const automationKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "automation"] as const,
  rules: (workspaceId: string) => [...automationKeys.all(workspaceId), "rules"] as const,
  rule: (workspaceId: string, ruleId: string) =>
    [...automationKeys.all(workspaceId), "rule", ruleId] as const,
  logs: (workspaceId: string) => [...automationKeys.all(workspaceId), "logs"] as const,
};

export const notificationKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "notifications"] as const,
  list: (workspaceId: string, unreadOnly = false) =>
    [...notificationKeys.all(workspaceId), "list", unreadOnly] as const,
};

export const settingsKeys = {
  all: (workspaceId: string) => [...workspaceScope(workspaceId), "settings"] as const,
  ai: (workspaceId: string) => [...settingsKeys.all(workspaceId), "ai"] as const,
  aiModels: (workspaceId: string) => [...settingsKeys.all(workspaceId), "ai-models"] as const,
  aiProviders: (workspaceId: string) => [...settingsKeys.all(workspaceId), "ai-providers"] as const,
  email: (workspaceId: string) => [...settingsKeys.all(workspaceId), "email"] as const,
  document: (workspaceId: string) => [...settingsKeys.all(workspaceId), "document"] as const,
};

export const invalidateWorkspace = async (
  queryClient: QueryClient,
  workspaceId: string,
): Promise<void> => {
  await queryClient.invalidateQueries({ queryKey: workspaceScope(workspaceId) });
};

export const keepPreviousWithinWorkspace =
  <TData,>(workspaceId: string) =>
  (
    previousData: TData | undefined,
    previousQuery: Query | undefined,
  ): TData | undefined => {
    const previousWorkspaceId = previousQuery?.queryKey?.[1];
    return previousWorkspaceId === workspaceId ? previousData : undefined;
  };
