/** Phase 1 — batch operations. Every route is gated on capability.batch_dispatch. */
import { apiClient } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  BatchAnalytics,
  BatchDetail,
  BatchList,
  BatchSummary,
  DispatchPolicy,
  DispatchResult,
  ExportPackage,
  HealResult,
  PackageList,
  PackageManifest,
  RetryResult,
  VerifyResult,
} from "@/types/batches";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const one = (workspaceId: string, batchId: string): string =>
  `${ws(workspaceId)}/processing-batches/${encodeURIComponent(batchId)}`;
const pkg = (workspaceId: string, packageId: string): string =>
  `${ws(workspaceId)}/export-packages/${encodeURIComponent(packageId)}`;

export const batchKeys = {
  all: (workspaceId: string) => ["batches", workspaceId] as const,
  list: (workspaceId: string, status: string) => ["batches", workspaceId, "list", status] as const,
  detail: (workspaceId: string, batchId: string) => ["batches", workspaceId, "batch", batchId] as const,
  analytics: (workspaceId: string, batchId: string) => ["batches", workspaceId, "analytics", batchId] as const,
  packages: (workspaceId: string, batchId?: string) => ["batches", workspaceId, "packages", batchId ?? "all"] as const,
  policy: (workspaceId: string) => ["batches", workspaceId, "policy"] as const,
};

export const listBatches = async (workspaceId: string, status?: "ACTIVE" | "ARCHIVED"): Promise<BatchList> =>
  (await apiClient.get<BatchList>(`${ws(workspaceId)}/processing-batches`, { params: status ? { status } : {} })).data;

export const createBatch = async (
  workspaceId: string,
  body: { name: string; description?: string; work_item_ids?: readonly string[]; ingestion_batch_id?: string },
): Promise<BatchSummary> => (await apiClient.post<BatchSummary>(`${ws(workspaceId)}/processing-batches`, body)).data;

export const getBatch = async (workspaceId: string, batchId: string): Promise<BatchDetail> =>
  (await apiClient.get<BatchDetail>(one(workspaceId, batchId))).data;

export const updateBatch = async (
  workspaceId: string,
  batchId: string,
  body: { name?: string; description?: string; status?: "ACTIVE" | "ARCHIVED" },
): Promise<BatchSummary> => (await apiClient.patch<BatchSummary>(one(workspaceId, batchId), body)).data;

export const deleteBatch = async (workspaceId: string, batchId: string): Promise<void> => {
  await apiClient.delete(one(workspaceId, batchId));
};

export const addBatchDocuments = async (
  workspaceId: string,
  batchId: string,
  workItemIds: readonly string[],
): Promise<BatchSummary> =>
  (await apiClient.post<BatchSummary>(`${one(workspaceId, batchId)}/documents`, { work_item_ids: workItemIds })).data;

export const removeBatchDocument = async (workspaceId: string, batchId: string, workItemId: string): Promise<void> => {
  await apiClient.delete(`${one(workspaceId, batchId)}/documents/${encodeURIComponent(workItemId)}`);
};

export const getBatchAnalytics = async (workspaceId: string, batchId: string): Promise<BatchAnalytics> =>
  (await apiClient.get<BatchAnalytics>(`${one(workspaceId, batchId)}/analytics`)).data;

export const healBatch = async (workspaceId: string, batchId: string, workItemIds?: readonly string[]): Promise<HealResult> =>
  (await apiClient.post<HealResult>(`${one(workspaceId, batchId)}/heal`, { work_item_ids: workItemIds ?? [] })).data;

export const dispatchBatch = async (workspaceId: string, batchId: string): Promise<DispatchResult> =>
  (await apiClient.post<DispatchResult>(`${one(workspaceId, batchId)}/dispatch`)).data;

export const retryFailed = async (workspaceId: string, batchId: string): Promise<RetryResult> =>
  (await apiClient.post<RetryResult>(`${one(workspaceId, batchId)}/retry-failed`)).data;

export const getDispatchPolicy = async (workspaceId: string): Promise<DispatchPolicy> =>
  (await apiClient.get<DispatchPolicy>(`${ws(workspaceId)}/dispatch-policy`)).data;

export const saveDispatchPolicy = async (
  workspaceId: string,
  body: Pick<DispatchPolicy, "straight_through_min_confidence" | "review_min_confidence" | "require_required_fields" | "tag_documents">,
): Promise<DispatchPolicy> => (await apiClient.put<DispatchPolicy>(`${ws(workspaceId)}/dispatch-policy`, body)).data;

export const listPackages = async (workspaceId: string, batchId?: string): Promise<PackageList> =>
  (await apiClient.get<PackageList>(`${ws(workspaceId)}/export-packages`, { params: batchId ? { batch_id: batchId } : {} })).data;

export const requestPackage = async (
  workspaceId: string,
  body: { name?: string; batch_id?: string; work_item_ids?: readonly string[]; include_originals: boolean },
): Promise<ExportPackage> => (await apiClient.post<ExportPackage>(`${ws(workspaceId)}/export-packages`, body)).data;

export const getPackageManifest = async (workspaceId: string, packageId: string): Promise<PackageManifest> =>
  (await apiClient.get<PackageManifest>(`${pkg(workspaceId, packageId)}/manifest`)).data;

/** The archive goes through the API client: it is authorised by the session, not a public link. */
export const downloadPackage = async (workspaceId: string, item: ExportPackage): Promise<void> => {
  const response = await apiClient.get<Blob>(`${pkg(workspaceId, item.id)}/download`, {
    responseType: "blob",
    timeout: 300_000,
  });
  const safe = item.name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "export-package";
  downloadBlob(response.data, `${safe}-${item.id.slice(0, 8)}.zip`);
};

export const verifyPackage = async (workspaceId: string, file: File): Promise<VerifyResult> => {
  const form = new FormData();
  form.append("file", file);
  return (
    await apiClient.post<VerifyResult>(`${ws(workspaceId)}/export-packages/verify`, form, { timeout: 300_000 })
  ).data;
};
