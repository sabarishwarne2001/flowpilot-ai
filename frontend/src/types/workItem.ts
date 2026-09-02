/**
 * Work Item Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * ARCH-0V Tranche 4 — `UploadDocumentResponse` no longer lies.
 *
 * It previously read:
 *
 *     export type UploadDocumentResponse = WorkItemResponse & {
 *       readonly work_item?: WorkItemResponse;
 *       readonly message?: string;
 *     };
 *
 * The upload route is `@router.post("", response_model=WorkItemResponse)` in
 * `app/api/v1/work_items.py` and has always returned the flat object. Neither
 * optional field has ever had a server-side source.
 *
 * What made this survive four phases is that it is not wrong enough to fail.
 * Both fields were optional, so `response.work_item` type-checks perfectly and
 * evaluates to `undefined` forever — and `UploadDropzone.tsx` carried a
 * defensive branch reading exactly that. The type described a response shape
 * that never existed, and the runtime code defended against it.
 *
 * Gate 0V-G8 now asserts that no frontend response type declares a field its
 * `response_model` omits, which is the mechanism that stops the next one.
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

/**
 * The upload endpoint returns a work item. Kept as a named alias rather than
 * collapsed into `WorkItemResponse` at the call sites, so that if the upload
 * response ever legitimately diverges there is a type to widen — and the
 * widening shows up in review as a change to this line.
 */
export type UploadDocumentResponse = WorkItemResponse;

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
