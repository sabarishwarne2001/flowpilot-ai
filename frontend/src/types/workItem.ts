/**
 * Work Item Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * These interfaces mirror the backend Pydantic models and provide
 * strict compile-time contracts between the React frontend and
 * FastAPI backend.
 */

/* --------------------------------------------------------------------------
 * Shared Types
 * -------------------------------------------------------------------------- */

/**
 * Current processing state of a work item.
 */
export type WorkItemStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";

/**
 * Allowed sortable backend fields.
 */
export type WorkItemSortField =
  | "created_at"
  | "updated_at"
  | "original_filename"
  | "file_size"
  | "status";

/**
 * Structured AI extraction payload.
 *
 * This interface can grow alongside backend entity extraction
 * without requiring changes across the frontend.
 */
export interface ExtractedEntities {
  readonly people?: readonly string[];
  readonly organizations?: readonly string[];
  readonly locations?: readonly string[];
  readonly dates?: readonly string[];
  readonly emails?: readonly string[];
  readonly phone_numbers?: readonly string[];
  readonly urls?: readonly string[];

  /**
   * Future entity categories.
   */
  readonly [key: string]: readonly string[] | undefined;
}

/* --------------------------------------------------------------------------
 * Response DTOs
 * -------------------------------------------------------------------------- */

/**
 * Work Item returned by the backend.
 *
 * All timestamps are UTC ISO-8601 strings.
 */
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
/**
 * Upload endpoint response.
 */
export interface UploadDocumentResponse {
  readonly work_item: WorkItemResponse;
  readonly message: string;
}

/**
 * Query parameters used when requesting paginated
 * work item lists from the backend.
 */
export interface WorkItemQueryFilters {
  readonly page: number;
  readonly pageSize: number;

  readonly search?: string;

  readonly status?: WorkItemStatus;

  readonly sortBy?: WorkItemSortField;

  readonly sortOrder?: "asc" | "desc";
}

/**
 * Paginated response returned by the backend.
 */
export interface WorkItemsListResponse {
  readonly items: readonly WorkItemResponse[];

  readonly total: number;

  readonly page: number;

  readonly pageSize: number;

  readonly totalPages: number;
}
/**
 * Internal upload request metadata.
 *
 * Used by the frontend before sending a multipart/form-data
 * upload request to the backend.
 */
export interface WorkItemCreateRequest {
  readonly original_filename: string;
  readonly stored_filename: string;
  readonly file_type: string;
  readonly file_size: number;
}
