/**
 * Query key factories for FlowPilot AI.
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

export const organizationScope = (organizationId: string) =>
  ["org", organizationId] as const;

export const organizationKeys = {
  all: (organizationId: string) => organizationScope(organizationId),
  members: (organizationId: string, includeInactive: boolean) =>
    [...organizationScope(organizationId), "members", { includeInactive }] as const,
};

export const sessionKeys = {
  all: ["sessions"] as const,
  list: () => [...sessionKeys.all, "list"] as const,
};

export const apiKeyKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "api-keys"] as const,
  list: (organizationId: string) =>
    [...apiKeyKeys.all(organizationId), "list"] as const,
};

export const webhookKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "webhooks"] as const,
  endpoints: (organizationId: string) =>
    [...webhookKeys.all(organizationId), "endpoints"] as const,
  deliveries: (organizationId: string, endpointId: string, status?: string) =>
    [...webhookKeys.all(organizationId), "deliveries", endpointId, { status }] as const,
  attempts: (organizationId: string, deliveryId: string) =>
    [...webhookKeys.all(organizationId), "attempts", deliveryId] as const,
};

export const ownershipKeys = {
  mine: ["ownership-transfers", "mine"] as const,
};

export const billingKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "billing"] as const,
  plans: (organizationId: string) =>
    [...billingKeys.all(organizationId), "plans"] as const,
  subscription: (organizationId: string) =>
    [...billingKeys.all(organizationId), "subscription"] as const,
  access: (organizationId: string) =>
    [...billingKeys.all(organizationId), "access"] as const,
  invoices: (organizationId: string) =>
    [...billingKeys.all(organizationId), "invoices"] as const,
  invoice: (organizationId: string, invoiceId: string) =>
    [...billingKeys.all(organizationId), "invoice", invoiceId] as const,
  invoiceReproduction: (organizationId: string, invoiceId: string) =>
    [...billingKeys.all(organizationId), "invoice", invoiceId, "reproduction"] as const,
};

export const usageKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "usage"] as const,
  summary: (organizationId: string, period: string) =>
    [...usageKeys.all(organizationId), "summary", period] as const,
  series: (organizationId: string, from: string, granularity: string) =>
    [...usageKeys.all(organizationId), "series", from, granularity] as const,
  limits: (organizationId: string) =>
    [...usageKeys.all(organizationId), "limits"] as const,
};

export const identityKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "identity"] as const,
  domains: (organizationId: string) =>
    [...identityKeys.all(organizationId), "domains"] as const,
  idpConfigs: (organizationId: string) =>
    [...identityKeys.all(organizationId), "idp-configs"] as const,
  scimKeys: (organizationId: string) =>
    [...identityKeys.all(organizationId), "scim-keys"] as const,
  securityPolicy: (organizationId: string) =>
    [...identityKeys.all(organizationId), "security-policy"] as const,
  directory: (organizationId: string) =>
    [...identityKeys.all(organizationId), "directory"] as const,
};

export const auditKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "audit"] as const,
  list: (organizationId: string, filters: Record<string, unknown>) =>
    [...auditKeys.all(organizationId), "list", filters] as const,
  detail: (organizationId: string, auditLogId: string) =>
    [...auditKeys.all(organizationId), "detail", auditLogId] as const,
};

export const verificationKeys = {
  all: (workspaceId: string) =>
    [...workspaceScope(workspaceId), "verifications"] as const,
  list: (workspaceId: string, status: string | undefined) =>
    [...verificationKeys.all(workspaceId), "list", status ?? "ALL"] as const,
  detail: (workspaceId: string, verificationId: string) =>
    [...verificationKeys.all(workspaceId), "detail", verificationId] as const,
};

export const invalidateOrganization = async (
  queryClient: QueryClient,
  organizationId: string,
): Promise<void> => {
  await queryClient.invalidateQueries({
    queryKey: organizationScope(organizationId),
  });
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
    previousQuery: any,
  ): TData | undefined => {
    const previousWorkspaceId = previousQuery?.queryKey?.[1];
    return previousWorkspaceId === workspaceId ? previousData : undefined;
  };
