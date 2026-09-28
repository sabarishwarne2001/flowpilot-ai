/**
 * ARCH48-S2:collab-api. Real-time collaborative review over REST (Enterprise).
 *
 * The live channel itself is a WebSocket (`hooks/useLiveReview.ts`); these are
 * the reads and writes it announces: discussion threads and comments, the
 * paragraphs a thread can anchor to, the live state for a console whose socket
 * is down, and the workspace admin's lock break. Every route is gated on the
 * collaborative-review capability (402 otherwise).
 */

import api, { API_BASE_URL } from "./client";
import { REVIEW_ENDPOINTS } from "./endpoints";

import type {
  BreakLockResult,
  CommentCreated,
  EditComment,
  LiveState,
  NewComment,
  NewThread,
  ParagraphList,
  ThreadCreated,
  ThreadList,
  ThreadOut,
} from "@/types/collab";
import { LIVE_SUFFIX } from "@/types/collab";
import type { ReviewKind } from "@/types/review";

export async function getLiveState(workspaceId: string): Promise<LiveState> {
  const response = await api.get<LiveState>(REVIEW_ENDPOINTS.collabState(workspaceId));
  return response.data;
}

export async function listThreads(workspaceId: string, kind: ReviewKind, itemId: string): Promise<ThreadList> {
  const response = await api.get<ThreadList>(REVIEW_ENDPOINTS.collabThreads(workspaceId, kind, itemId));
  return response.data;
}

export async function startThread(
  workspaceId: string,
  kind: ReviewKind,
  itemId: string,
  body: NewThread,
): Promise<ThreadCreated> {
  const response = await api.post<ThreadCreated>(REVIEW_ENDPOINTS.collabThreads(workspaceId, kind, itemId), body);
  return response.data;
}

export async function listParagraphs(workspaceId: string, kind: ReviewKind, itemId: string): Promise<ParagraphList> {
  const response = await api.get<ParagraphList>(REVIEW_ENDPOINTS.collabParagraphs(workspaceId, kind, itemId));
  return response.data;
}

export async function replyToThread(workspaceId: string, threadId: string, body: NewComment): Promise<CommentCreated> {
  const response = await api.post<CommentCreated>(REVIEW_ENDPOINTS.collabComments(workspaceId, threadId), body);
  return response.data;
}

export async function resolveThread(workspaceId: string, threadId: string): Promise<ThreadOut> {
  const response = await api.post<ThreadOut>(REVIEW_ENDPOINTS.collabThreadResolve(workspaceId, threadId));
  return response.data;
}

export async function reopenThread(workspaceId: string, threadId: string): Promise<ThreadOut> {
  const response = await api.post<ThreadOut>(REVIEW_ENDPOINTS.collabThreadReopen(workspaceId, threadId));
  return response.data;
}

export async function editComment(workspaceId: string, commentId: string, body: EditComment): Promise<ThreadOut> {
  const response = await api.patch<ThreadOut>(REVIEW_ENDPOINTS.collabComment(workspaceId, commentId), body);
  return response.data;
}

export async function deleteComment(workspaceId: string, commentId: string): Promise<ThreadOut> {
  const response = await api.delete<ThreadOut>(REVIEW_ENDPOINTS.collabComment(workspaceId, commentId));
  return response.data;
}

export async function breakLock(workspaceId: string, kind: ReviewKind, itemId: string): Promise<BreakLockResult> {
  const response = await api.post<BreakLockResult>(REVIEW_ENDPOINTS.collabBreakLock(workspaceId, kind, itemId));
  return response.data;
}

/**
 * The live channel's absolute ws:// or wss:// URL. API_BASE_URL is relative in
 * production (`/api/v1`, same origin behind Caddy, custom domains included) and
 * absolute in development; both resolve against the page's origin, and the
 * scheme follows the API's (https -> wss).
 */
export function liveUrl(workspaceId: string): string {
  const base = new URL(API_BASE_URL, window.location.origin);
  const scheme = base.protocol === "https:" ? "wss:" : "ws:";
  const path = `${base.pathname.replace(/\/$/, "")}${REVIEW_ENDPOINTS.queue(workspaceId)}${LIVE_SUFFIX}`;
  return `${scheme}//${base.host}${path}`;
}
