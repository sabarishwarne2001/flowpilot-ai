/**
 * Work Item Data Transfer Objects (DTOs) for FlowPilot AI.
 */

export type WorkItemStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";

export type WorkItemSortField =
  | "created_at"
  | "updated_at"
  | "original_filename"
  | "file_size"
  | "status";

export interface ExtractedEntities {
  readonly people?: readonly string[];
  readonly organizations?: readonly string[];
  readonly locations?: readonly string[];
  readonly dates?: readonly string[];
  readonly emails?: readonly string[];
  readonly phone_numbers?: readonly string[];
  readonly urls?: readonly string[];
  readonly [key: string]: readonly string[] | undefined;
}

export interface WorkItemResponse {
  readonly id: string;
  readonly original_filename: string;
  readonly stored_filename: string;
  readonly file_type: string;
  readonly file_size: number;
  readonly status: WorkItemStatus;
  readonly summary: string | null;
  readonly extracted_entities: ExtractedEntities | null;
  readonly user_id: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export type UploadDocumentResponse = WorkItemResponse & {
  readonly work_item?: WorkItemResponse;
  readonly message?: string;
};

export interface WorkItemQueryFilters {
  readonly page: number;
  readonly pageSize: number;
  readonly search?: string;
  readonly status?: WorkItemStatus;
  readonly sortBy?: WorkItemSortField;
  readonly sortOrder?: "asc" | "desc";
}

export interface WorkItemsListResponse {
  readonly items: readonly WorkItemResponse[];
  readonly total: number;
  readonly page: number;
  readonly pageSize: number;
  readonly totalPages: number;
}

export interface WorkItemCreateRequest {
  readonly original_filename: string;
  readonly stored_filename: string;
  readonly file_type: string;
  readonly file_size: number;
}

export interface ReindexResult {
  readonly queued: number;
  readonly total_documents: number;
  readonly detail: string;
}
