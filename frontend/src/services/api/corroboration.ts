/** ARCH45-S2:api-client — the document corroborator. Every route is gated on capability.universal_corroborator. */
import { apiClient } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  CorroborationRequest, Decision, DiscrepancyRow, DocumentComparisons, ExportFormat, RequestResult, ReviewRunResult,
  RunDetail, RunList, RunStatus, RunVerdict, WorkspaceRules,
} from "@/types/corroboration";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}/corroboration`;
const one = (workspaceId: string, runId: string): string => `${ws(workspaceId)}/runs/${encodeURIComponent(runId)}`;

export const corroborationKeys = {
  all: (workspaceId: string) => ["corroboration", workspaceId] as const,
  list: (workspaceId: string, status?: RunStatus) => ["corroboration", workspaceId, "list", status ?? "ALL"] as const,
  detail: (workspaceId: string, runId: string) => ["corroboration", workspaceId, "run", runId] as const,
  rules: (workspaceId: string) => ["corroboration", workspaceId, "rules"] as const,
  document: (workspaceId: string, workItemId: string) => ["corroboration", workspaceId, "document", workItemId] as const,
};

export const listRuns = async (workspaceId: string, status?: RunStatus): Promise<RunList> =>
  (await apiClient.get<RunList>(`${ws(workspaceId)}/runs`, { params: status ? { status } : {} })).data;

/** 200 with cached=true when a current answer exists; 202 when the comparison is queued. */
export const requestRun = async (workspaceId: string, body: CorroborationRequest): Promise<RequestResult> =>
  (await apiClient.post<RequestResult>(`${ws(workspaceId)}/runs`, body, { timeout: 120_000 })).data;

export const getRun = async (workspaceId: string, runId: string): Promise<RunDetail> =>
  (await apiClient.get<RunDetail>(one(workspaceId, runId), { timeout: 60_000 })).data;

export const refreshRun = async (workspaceId: string, runId: string): Promise<RequestResult> =>
  (await apiClient.post<RequestResult>(`${one(workspaceId, runId)}/refresh`, undefined, { timeout: 120_000 })).data;

export const decideDiscrepancy = async (
  workspaceId: string, runId: string, discrepancyId: string, status: Decision, note?: string | null,
): Promise<DiscrepancyRow> =>
  (await apiClient.patch<DiscrepancyRow>(`${one(workspaceId, runId)}/discrepancies/${encodeURIComponent(discrepancyId)}`,
    note === undefined ? { status } : { status, note })).data;

export const reviewRun = async (workspaceId: string, runId: string, verdict: RunVerdict): Promise<ReviewRunResult> =>
  (await apiClient.post<ReviewRunResult>(`${one(workspaceId, runId)}/review`, { verdict })).data;

export const deleteRun = async (workspaceId: string, runId: string): Promise<void> => {
  await apiClient.delete(one(workspaceId, runId));
};

export const getWorkspaceRules = async (workspaceId: string): Promise<WorkspaceRules> =>
  (await apiClient.get<WorkspaceRules>(`${ws(workspaceId)}/rules`)).data;

export const getDocumentComparisons = async (workspaceId: string, workItemId: string): Promise<DocumentComparisons> =>
  (await apiClient.get<DocumentComparisons>(
    `/workspaces/${encodeURIComponent(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/corroboration`)).data;

/** The path of one page image; fetched with the session by useAuthorizedBlobUrl, never as a bare <img src>. */
export const pageImagePath = (workspaceId: string, runId: string, workItemId: string, page: number, dpi = 100): string =>
  `${one(workspaceId, runId)}/documents/${encodeURIComponent(workItemId)}/pages/${page}/image?dpi=${dpi}`;

/** The PDF report (or CSV / JSON), authorised by the session, so it goes through the API client. */
export const downloadReport = async (workspaceId: string, runId: string, format: ExportFormat, stem: string): Promise<void> => {
  const response = await apiClient.get<Blob>(`${one(workspaceId, runId)}/export`, {
    params: { format }, responseType: "blob", timeout: 180_000,
  });
  const safe = (stem.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "_") || "comparison").slice(0, 60);
  downloadBlob(response.data, `corroboration_${safe}.${format}`);
};
