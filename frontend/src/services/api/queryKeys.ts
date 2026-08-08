/**
 * Query key factories. Every tenant-scoped key begins ["ws", workspaceId].
 *
 * Centralized so that omitting the workspace from a key is not something you
 * can do by forgetting — there is no builder that produces a scoped key
 * without one. That prefix also makes invalidation and eviction naturally
 * workspace-bounded: React Query matches keys by prefix, so
 * ["ws", id] reaches everything in one workspace and nothing in another.
 */

import type { QueryClient } from "@tanstack/react-query";
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

/**
 * Invalidates every cached query for one workspace.
 */
export const invalidateWorkspace = async (
  queryClient: QueryClient,
  workspaceId: string,
): Promise<void> => {
  await queryClient.invalidateQueries({ queryKey: workspaceScope(workspaceId) });
};

/**
 * placeholderData that survives a filter change but not a workspace change.
 * Uses 'any' to bypass TanStack Query's strict generic parameter validation.
 */
export const keepPreviousWithinWorkspace =
  <TData,>(workspaceId: string) =>
  (
    previousData: TData | undefined,
    previousQuery: any,
  ): TData | undefined => {
    const previousWorkspaceId = previousQuery?.queryKey?.[1];
    return previousWorkspaceId === workspaceId ? previousData : undefined;
  };
