import type { AxiosProgressEvent, AxiosResponse } from "axios";
import apiClient from "@/services/api/client";
import { WORK_ITEM_ENDPOINTS } from "@/services/api/endpoints";
import type {
  UploadDocumentResponse,
  WorkItemQueryFilters,
  WorkItemsListResponse,
  WorkItemResponse,
} from "@/types/workItem";

const JSON_HEADERS = { Accept: "application/json" } as const;

const buildQueryParams = (filters: WorkItemQueryFilters): URLSearchParams => {
  const params = new URLSearchParams({
    page: String(filters.page),
    pageSize: String(filters.pageSize),
  });
  if (filters.search?.trim()) params.set("search", filters.search.trim());
  if (filters.status) params.set("status", filters.status);
  if (filters.sortBy) params.set("sortBy", filters.sortBy);
  if (filters.sortOrder) params.set("sortOrder", filters.sortOrder);
  return params;
};

export const uploadDocument = async (
  workspaceId: string,
  file: File,
  onUploadProgress?: (progressEvent: AxiosProgressEvent) => void,
): Promise<UploadDocumentResponse> => {
  const formData = new FormData();
  formData.append("file", file);

  const response = await apiClient.post<
    UploadDocumentResponse,
    AxiosResponse<UploadDocumentResponse>
  >(WORK_ITEM_ENDPOINTS.upload(workspaceId), formData, {
    headers: { "Content-Type": "multipart/form-data", ...JSON_HEADERS },
    ...(onUploadProgress ? { onUploadProgress } : {}),
  });
  return response.data;
};

export const getWorkItems = async (
  workspaceId: string,
  filters: WorkItemQueryFilters,
): Promise<WorkItemsListResponse> => {
  const response = await apiClient.get<WorkItemsListResponse>(
    WORK_ITEM_ENDPOINTS.list(workspaceId),
    { params: buildQueryParams(filters), headers: JSON_HEADERS },
  );
  return response.data;
};

export const getWorkItemDetails = async (
  workspaceId: string,
  workItemId: string,
): Promise<WorkItemResponse> => {
  const response = await apiClient.get<WorkItemResponse>(
    WORK_ITEM_ENDPOINTS.details(workspaceId, workItemId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const reprocessWorkItem = async (
  workspaceId: string,
  workItemId: string,
): Promise<void> => {
  await apiClient.post(
    WORK_ITEM_ENDPOINTS.reprocess(workspaceId, workItemId),
    null,
    { headers: JSON_HEADERS },
  );
};

export const deleteWorkItem = async (
  workspaceId: string,
  workItemId: string,
): Promise<void> => {
  await apiClient.delete(WORK_ITEM_ENDPOINTS.remove(workspaceId, workItemId), {
    headers: JSON_HEADERS,
  });
};

export const resetKnowledgeBase = async (
  workspaceId: string,
): Promise<void> => {
  await apiClient.delete(WORK_ITEM_ENDPOINTS.knowledgeBase(workspaceId), {
    headers: JSON_HEADERS,
  });
};

export const workItemApi = {
  uploadDocument,
  getWorkItems,
  getWorkItemDetails,
  reprocessWorkItem,
  deleteWorkItem,
  resetKnowledgeBase,
};

export default workItemApi;
