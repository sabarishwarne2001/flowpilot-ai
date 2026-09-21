/**
 * ARCH40-S2:review-api. The unified review hub.
 *
 * Every function takes the workspace explicitly, for the reason
 * `aiSettings.ts` gives: a hidden "current workspace" is the implicit-tenant
 * pattern ARCH-01 removed, and the compiler enumerates every caller when the
 * contract changes.
 */

import api from "./client";
import { REVIEW_ENDPOINTS } from "./endpoints";

import type {
  ReviewAssignee,
  ReviewBulkRequest,
  ReviewBulkResponse,
  ReviewKind,
  ReviewQueue,
  ReviewQueueFilters,
  ReviewResolveRequest,
  ReviewResolveResponse,
} from "@/types/review";

/** Repeated keys, as FastAPI reads list query parameters. */
const toParams = (filters: ReviewQueueFilters): URLSearchParams => {
  const params = new URLSearchParams();
  filters.kind?.forEach((value) => params.append("kind", value));
  filters.severity?.forEach((value) => params.append("severity", value));
  filters.reason?.forEach((value) => params.append("reason", value));
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.tag) {
    params.set("tag", filters.tag);
  }
  if (filters.assignee_user_id) {
    params.set("assignee_user_id", filters.assignee_user_id);
  }
  if (filters.unassigned_only) {
    params.set("unassigned_only", "true");
  }
  if (filters.min_age_seconds !== undefined) {
    params.set("min_age_seconds", String(filters.min_age_seconds));
  }
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", String(filters.page_size ?? 25));
  return params;
};

export async function listReviews(
  workspaceId: string,
  filters: ReviewQueueFilters,
): Promise<ReviewQueue> {
  const response = await api.get<ReviewQueue>(REVIEW_ENDPOINTS.queue(workspaceId), {
    params: toParams(filters),
  });
  return response.data;
}

export async function listReviewAssignees(workspaceId: string): Promise<ReviewAssignee[]> {
  const response = await api.get<ReviewAssignee[]>(REVIEW_ENDPOINTS.assignees(workspaceId));
  return response.data;
}

export async function resolveReview(
  workspaceId: string,
  kind: ReviewKind,
  itemId: string,
  body: ReviewResolveRequest,
): Promise<ReviewResolveResponse> {
  const response = await api.post<ReviewResolveResponse>(
    REVIEW_ENDPOINTS.resolve(workspaceId, kind, itemId),
    body,
  );
  return response.data;
}

export async function assignReview(
  workspaceId: string,
  kind: ReviewKind,
  itemId: string,
  assigneeUserId: string,
): Promise<void> {
  await api.post(REVIEW_ENDPOINTS.assign(workspaceId, kind, itemId), {
    assignee_user_id: assigneeUserId,
  });
}

export async function unassignReview(
  workspaceId: string,
  kind: ReviewKind,
  itemId: string,
): Promise<void> {
  await api.delete(REVIEW_ENDPOINTS.assign(workspaceId, kind, itemId));
}

export async function bulkReview(
  workspaceId: string,
  body: ReviewBulkRequest,
): Promise<ReviewBulkResponse> {
  const response = await api.post<ReviewBulkResponse>(REVIEW_ENDPOINTS.bulk(workspaceId), body);
  return response.data;
}
