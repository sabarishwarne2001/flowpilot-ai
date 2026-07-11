import type { AxiosProgressEvent } from "axios";

import apiClient from "@/services/api/client";

import type {
  UploadDocumentResponse,
  WorkItemQueryFilters,
  WorkItemsListResponse,
  WorkItemResponse,
} from "@/types/workItem";

/* --------------------------------------------------------------------------
 * API Endpoints
 * -------------------------------------------------------------------------- */

const WORK_ITEM_ENDPOINTS = {
  upload: "/work-items/upload",
  list: "/work-items",
  details: (id: string) => `/work-items/${id}`,
  reprocess: (id: string) => `/work-items/${id}/reprocess`,
  remove: (id: string) => `/work-items/${id}`,
} as const;

/* --------------------------------------------------------------------------
 * Helpers
 * -------------------------------------------------------------------------- */

const buildQueryParams = (filters: WorkItemQueryFilters): URLSearchParams => {
  const params = new URLSearchParams({
    page: String(filters.page),
    pageSize: String(filters.pageSize),
  });

  if (filters.search?.trim()) {
    params.set("search", filters.search.trim());
  }

  if (filters.status) {
    params.set("status", filters.status);
  }

  if (filters.sortBy) {
    params.set("sortBy", filters.sortBy);
  }

  if (filters.sortOrder) {
    params.set("sortOrder", filters.sortOrder);
  }

  return params;
};
/**
 * Uploads a document for processing.
 */
export const uploadDocument = async (
  file: File,
  onUploadProgress?: (progressEvent: AxiosProgressEvent) => void
): Promise<UploadDocumentResponse> => {
  const formData = new FormData();

  formData.append("file", file);

  const requestConfig = {
    headers: {
      "Content-Type": "multipart/form-data",
      Accept: "application/json",
    },

    ...(onUploadProgress
      ? {
          onUploadProgress,
        }
      : {}),
  };

  const response = await apiClient.post<
    UploadDocumentResponse,
    import("axios").AxiosResponse<UploadDocumentResponse>
  >(WORK_ITEM_ENDPOINTS.upload, formData, requestConfig);

  return response.data;
};

/**
 * Returns a paginated work item list.
 */
export const getWorkItems = async (
  filters: WorkItemQueryFilters
): Promise<WorkItemsListResponse> => {
  const response = await apiClient.get<WorkItemsListResponse>(
    WORK_ITEM_ENDPOINTS.list,
    {
      params: buildQueryParams(filters),
      headers: {
        Accept: "application/json",
      },
    }
  );

  return response.data;
};
/**
 * Returns the details for a single work item.
 */
export const getWorkItemDetails = async (
  workItemId: string
): Promise<WorkItemResponse> => {
  const response = await apiClient.get<WorkItemResponse>(
    WORK_ITEM_ENDPOINTS.details(workItemId),
    {
      headers: {
        Accept: "application/json",
      },
    }
  );

  return response.data;
};

/**
 * Requeues a failed work item for processing.
 */
export const reprocessWorkItem = async (workItemId: string): Promise<void> => {
  await apiClient.post(WORK_ITEM_ENDPOINTS.reprocess(workItemId), null, {
    headers: {
      Accept: "application/json",
    },
  });
};

/**
 * Placeholder for future document deletion.
 */
export const deleteWorkItem = async (workItemId: string): Promise<void> => {
  await apiClient.delete(WORK_ITEM_ENDPOINTS.remove(workItemId), {
    headers: {
      Accept: "application/json",
    },
  });
};
/**
 * Unified Work Item API namespace.
 *
 * Groups all work item operations behind a single export,
 * simplifying imports and future dependency injection.
 */
export const workItemApi = {
  uploadDocument,
  getWorkItems,
  getWorkItemDetails,
  reprocessWorkItem,
  deleteWorkItem,
};

export default workItemApi;
