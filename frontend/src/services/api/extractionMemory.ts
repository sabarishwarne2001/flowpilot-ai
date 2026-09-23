/** ARCH41-S3:api-client — Extraction Memory. */
import { apiClient } from "@/services/api/client";
import type {
  MemoryMode,
  MemoryPotential,
  MemorySettings,
  MemorySummary,
  RuleRow,
  TemplateRow,
  TrialRow,
  WorkItemMemory,
} from "@/types/extractionMemory";

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/extraction-memory`;

export const getMemoryPotential = async (workspaceId: string): Promise<MemoryPotential> =>
  (await apiClient.get<MemoryPotential>(`${base(workspaceId)}/potential`)).data;

export const getMemorySummary = async (workspaceId: string): Promise<MemorySummary> =>
  (await apiClient.get<MemorySummary>(`${base(workspaceId)}/summary`)).data;

export const getMemorySettings = async (workspaceId: string): Promise<MemorySettings> =>
  (await apiClient.get<MemorySettings>(`${base(workspaceId)}/settings`)).data;

export const putMemorySettings = async (workspaceId: string, mode: MemoryMode): Promise<MemorySettings> =>
  (await apiClient.put<MemorySettings>(`${base(workspaceId)}/settings`, { mode })).data;

export const listMemoryTemplates = async (workspaceId: string): Promise<TemplateRow[]> =>
  (await apiClient.get<TemplateRow[]>(`${base(workspaceId)}/templates`)).data;

export const resetMemoryTemplate = async (workspaceId: string, templateId: string): Promise<TemplateRow> =>
  (
    await apiClient.patch<TemplateRow>(
      `${base(workspaceId)}/templates/${encodeURIComponent(templateId)}`,
      { action: "reset" },
    )
  ).data;

export const listMemoryRules = async (workspaceId: string): Promise<RuleRow[]> =>
  (await apiClient.get<RuleRow[]>(`${base(workspaceId)}/rules`)).data;

export const changeMemoryRule = async (
  workspaceId: string,
  ruleId: string,
  action: "retire" | "shadow",
): Promise<RuleRow> =>
  (await apiClient.patch<RuleRow>(`${base(workspaceId)}/rules/${encodeURIComponent(ruleId)}`, { action })).data;

export const listMemoryTrials = async (workspaceId: string): Promise<TrialRow[]> =>
  (await apiClient.get<TrialRow[]>(`${base(workspaceId)}/trials`)).data;

export const getWorkItemMemory = async (workspaceId: string, workItemId: string): Promise<WorkItemMemory> =>
  (
    await apiClient.get<WorkItemMemory>(
      `/workspaces/${encodeURIComponent(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/extraction-memory`,
    )
  ).data;

export const extractionMemoryKeys = {
  all: (workspaceId: string) => ["extraction-memory", workspaceId] as const,
  potential: (workspaceId: string) => ["extraction-memory", workspaceId, "potential"] as const,
  summary: (workspaceId: string) => ["extraction-memory", workspaceId, "summary"] as const,
  settings: (workspaceId: string) => ["extraction-memory", workspaceId, "settings"] as const,
  templates: (workspaceId: string) => ["extraction-memory", workspaceId, "templates"] as const,
  rules: (workspaceId: string) => ["extraction-memory", workspaceId, "rules"] as const,
  trials: (workspaceId: string) => ["extraction-memory", workspaceId, "trials"] as const,
  workItem: (workspaceId: string, workItemId: string) =>
    ["extraction-memory", workspaceId, "work-item", workItemId] as const,
};
