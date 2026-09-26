/** ARCH47-S2:api-client — ERP & system-of-record posting. Every route is gated on capability.erp_posting. */
import { apiClient } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  AcknowledgeRequest, CredentialSet, ErpCatalog, LookupCreate, LookupList, LookupRow, LookupUpdate, MappingCheck,
  MappingRow, MappingSave, MappingVersions, ObjectKind, OutcomeList, PostingDetail, PostingList, PostRequest,
  PostResponse, PreviewRequest, PreviewResponse, ReviewAction, TargetCreate, TargetDetail, TargetList, TargetRow,
  TargetUpdate, TestResult,
} from "@/types/erp";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const target = (workspaceId: string, id: string): string => `${ws(workspaceId)}/erp/targets/${encodeURIComponent(id)}`;
const posting = (workspaceId: string, id: string): string => `${ws(workspaceId)}/erp/postings/${encodeURIComponent(id)}`;
const mapping = (workspaceId: string, targetId: string, kind: ObjectKind): string =>
  `${target(workspaceId, targetId)}/mappings/${encodeURIComponent(kind)}`;

export interface PostingFilters {
  readonly state?: string;
  readonly target_id?: string;
  readonly object_kind?: string;
  readonly source_kind?: string;
  readonly source_id?: string;
  readonly work_item_id?: string;
  readonly q?: string;
  readonly limit?: number;
  readonly offset?: number;
}

export const erpKeys = {
  all: (workspaceId: string) => ["erp", workspaceId] as const,
  catalog: (workspaceId: string) => ["erp", workspaceId, "catalog"] as const,
  targets: (workspaceId: string) => ["erp", workspaceId, "targets"] as const,
  target: (workspaceId: string, id: string) => ["erp", workspaceId, "target", id] as const,
  mappings: (workspaceId: string, id: string, kind: string) => ["erp", workspaceId, "target", id, "mappings", kind] as const,
  lookups: (workspaceId: string) => ["erp", workspaceId, "lookups"] as const,
  outcomes: (workspaceId: string) => ["erp", workspaceId, "outcomes"] as const,
  postings: (workspaceId: string, filters: PostingFilters) => ["erp", workspaceId, "postings", filters] as const,
  posting: (workspaceId: string, id: string) => ["erp", workspaceId, "posting", id] as const,
  document: (workspaceId: string, workItemId: string) => ["erp", workspaceId, "document", workItemId] as const,
};

const clean = (filters: PostingFilters): Record<string, string | number> => {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      out[key] = value as string | number;
    }
  }
  return out;
};

// -- catalog and targets ---------------------------------------------------------------------

export const getErpCatalog = async (workspaceId: string): Promise<ErpCatalog> =>
  (await apiClient.get<ErpCatalog>(`${ws(workspaceId)}/erp/catalog`)).data;

export const listTargets = async (workspaceId: string): Promise<TargetList> =>
  (await apiClient.get<TargetList>(`${ws(workspaceId)}/erp/targets`)).data;

export const createTarget = async (workspaceId: string, body: TargetCreate): Promise<TargetDetail> =>
  (await apiClient.post<TargetDetail>(`${ws(workspaceId)}/erp/targets`, body)).data;

export const getTarget = async (workspaceId: string, id: string): Promise<TargetDetail> =>
  (await apiClient.get<TargetDetail>(target(workspaceId, id))).data;

export const updateTarget = async (workspaceId: string, id: string, body: TargetUpdate): Promise<TargetDetail> =>
  (await apiClient.patch<TargetDetail>(target(workspaceId, id), body)).data;

export const deleteTarget = async (workspaceId: string, id: string): Promise<void> => {
  await apiClient.delete(target(workspaceId, id));
};

export const setTargetCredential = async (workspaceId: string, id: string, body: CredentialSet): Promise<TargetRow> =>
  (await apiClient.put<TargetRow>(`${target(workspaceId, id)}/credential`, body)).data;

export const testTarget = async (workspaceId: string, id: string): Promise<TestResult> =>
  (await apiClient.post<TestResult>(`${target(workspaceId, id)}/test`, {}, { timeout: 90_000 })).data;

// -- mappings ------------------------------------------------------------------------------------

export const getMappings = async (workspaceId: string, targetId: string, kind: ObjectKind): Promise<MappingVersions> =>
  (await apiClient.get<MappingVersions>(mapping(workspaceId, targetId, kind))).data;

export const saveMapping = async (workspaceId: string, targetId: string, kind: ObjectKind, body: MappingSave): Promise<MappingRow> =>
  (await apiClient.put<MappingRow>(mapping(workspaceId, targetId, kind), body)).data;

export const validateMapping = async (workspaceId: string, targetId: string, kind: ObjectKind, body: MappingSave): Promise<MappingCheck> =>
  (await apiClient.post<MappingCheck>(`${mapping(workspaceId, targetId, kind)}/validate`, body)).data;

export const restoreDefaultMapping = async (workspaceId: string, targetId: string, kind: ObjectKind): Promise<MappingRow> =>
  (await apiClient.post<MappingRow>(`${mapping(workspaceId, targetId, kind)}/default`, {})).data;

// -- lookup tables ------------------------------------------------------------------------------

export const listLookups = async (workspaceId: string): Promise<LookupList> =>
  (await apiClient.get<LookupList>(`${ws(workspaceId)}/erp/lookups`)).data;

export const createLookup = async (workspaceId: string, body: LookupCreate): Promise<LookupRow> =>
  (await apiClient.post<LookupRow>(`${ws(workspaceId)}/erp/lookups`, body)).data;

export const updateLookup = async (workspaceId: string, id: string, body: LookupUpdate): Promise<LookupRow> =>
  (await apiClient.patch<LookupRow>(`${ws(workspaceId)}/erp/lookups/${encodeURIComponent(id)}`, body)).data;

export const deleteLookup = async (workspaceId: string, id: string): Promise<void> => {
  await apiClient.delete(`${ws(workspaceId)}/erp/lookups/${encodeURIComponent(id)}`);
};

// -- outcomes and postings -------------------------------------------------------------------------

export const listOutcomes = async (workspaceId: string, limit = 100): Promise<OutcomeList> =>
  (await apiClient.get<OutcomeList>(`${ws(workspaceId)}/erp/outcomes`, { params: { limit } })).data;

export const listPostings = async (workspaceId: string, filters: PostingFilters = {}): Promise<PostingList> =>
  (await apiClient.get<PostingList>(`${ws(workspaceId)}/erp/postings`, { params: clean(filters) })).data;

export const createPostings = async (workspaceId: string, body: PostRequest): Promise<PostResponse> =>
  (await apiClient.post<PostResponse>(`${ws(workspaceId)}/erp/postings`, body)).data;

export const previewPosting = async (workspaceId: string, body: PreviewRequest): Promise<PreviewResponse> =>
  (await apiClient.post<PreviewResponse>(`${ws(workspaceId)}/erp/postings/preview`, body)).data;

export const getPosting = async (workspaceId: string, id: string): Promise<PostingDetail> =>
  (await apiClient.get<PostingDetail>(posting(workspaceId, id))).data;

export const retryPosting = async (workspaceId: string, id: string, body: ReviewAction = {}): Promise<PostingDetail> =>
  (await apiClient.post<PostingDetail>(`${posting(workspaceId, id)}/retry`, body)).data;

export const acceptPosting = async (workspaceId: string, id: string, body: ReviewAction = {}): Promise<PostingDetail> =>
  (await apiClient.post<PostingDetail>(`${posting(workspaceId, id)}/accept`, body)).data;

export const cancelPosting = async (workspaceId: string, id: string, body: ReviewAction = {}): Promise<PostingDetail> =>
  (await apiClient.post<PostingDetail>(`${posting(workspaceId, id)}/cancel`, body)).data;

export const acknowledgePosting = async (workspaceId: string, id: string, body: AcknowledgeRequest): Promise<PostingDetail> =>
  (await apiClient.post<PostingDetail>(`${posting(workspaceId, id)}/acknowledge`, body)).data;

/** The rendered file, through the authenticated client (the download is audited server-side). */
export const downloadPostingFile = async (workspaceId: string, id: string, filename: string): Promise<void> => {
  const response = await apiClient.get<Blob>(`${posting(workspaceId, id)}/file`, { responseType: "blob", timeout: 120_000 });
  downloadBlob(response.data, filename || "posting");
};

export const documentPostings = async (workspaceId: string, workItemId: string): Promise<PostingList> =>
  (await apiClient.get<PostingList>(`${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/postings`)).data;
