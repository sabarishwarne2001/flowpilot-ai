/**
 * ARCH48-S2:collab-types. Real-time collaborative review: the REST wire shapes
 * (mirrors `backend/app/schemas/collab.py`) and the live channel's protocol
 * (mirrors `backend/app/services/collab/vocabulary.py`). `verify_arch48.py`
 * gate W3 compares the field lists, the event types and the close codes.
 */

import type { ReviewKind } from "@/types/review";

export const LIVE_SUBPROTOCOL = "flowpilot.review.v1";
export const LIVE_TOKEN_PREFIX = "bearer.";
export const LIVE_SUFFIX = "/collab/live";

export type AnchorKind = "ITEM" | "FIELD" | "PARAGRAPH";
export type AnchorState = "CURRENT" | "MOVED" | "OUTDATED";
export type ThreadStatus = "OPEN" | "RESOLVED";

export const ANCHOR_KINDS: readonly AnchorKind[] = ["ITEM", "FIELD", "PARAGRAPH"];
export const MAX_COMMENT_CHARS = 4000;
export const MAX_MENTIONS = 20;

/** Close codes the server uses. 1008 is a refused handshake (never accepted). */
export const CLOSE = {
  POLICY: 1008,
  TOO_BIG: 1009,
  GOING_AWAY: 1001,
  SERVER_ERROR: 1011,
  SESSION_ENDED: 4401,
  ACCESS_LOST: 4403,
  RATE_LIMITED: 4429,
} as const;

export type ClientMessageType = "view" | "ping" | "lock" | "unlock";
export const CLIENT_MESSAGE_TYPES: readonly ClientMessageType[] = ["view", "ping", "lock", "unlock"];

export type LiveEventType =
  | "presence"
  | "lock.acquired"
  | "lock.released"
  | "item.resolved"
  | "item.assigned"
  | "queue.changed"
  | "thread.changed";
export const LIVE_EVENT_TYPES: readonly LiveEventType[] = [
  "presence",
  "lock.acquired",
  "lock.released",
  "item.resolved",
  "item.assigned",
  "queue.changed",
  "thread.changed",
];

export type ServerMessageType = "hello" | "pong" | "lock.result" | "error" | LiveEventType;

export interface Person {
  readonly user_id: string;
  readonly email: string;
  readonly name: string;
}

export interface Viewer {
  readonly connection_id: string;
  readonly user_id: string;
  readonly name: string;
  readonly email: string;
  readonly kind: ReviewKind | null;
  readonly item_id: string | null;
  readonly since: number;
}

export interface LiveLock {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly holder_user_id: string;
  readonly holder?: Person | null;
  readonly acquired_at: string;
  readonly expires_at: string;
}

export interface HelloMessage {
  readonly type: "hello";
  readonly connection_id: string;
  readonly you: Person;
  readonly heartbeat_seconds: number;
  readonly lock_ttl_seconds: number;
  readonly presence: readonly Viewer[];
  readonly locks: readonly LiveLock[];
  readonly degraded: boolean;
  readonly allowed_kinds: readonly ReviewKind[];
}

export interface LockResultMessage {
  readonly type: "lock.result";
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly ok: boolean;
  readonly code?: string;
  readonly detail?: string;
  readonly holder?: Person | null;
  readonly expires_at?: string | null;
  readonly reentrant?: boolean;
  readonly released?: boolean;
}

export type ServerMessage =
  | HelloMessage
  | LockResultMessage
  | { readonly type: "pong"; readonly leases: readonly { kind: ReviewKind; item_id: string; expires_at: string }[]; readonly lost: readonly { kind: ReviewKind; item_id: string }[] }
  | { readonly type: "error"; readonly code: string; readonly detail: string }
  | { readonly type: "presence"; readonly viewers: readonly Viewer[] }
  | ({ readonly type: "lock.acquired" } & LiveLock)
  | { readonly type: "lock.released"; readonly kind: ReviewKind; readonly item_id: string; readonly holder_user_id: string | null; readonly reason: string }
  | { readonly type: "item.resolved"; readonly kind: ReviewKind; readonly item_id: string; readonly version: number; readonly by_user_id: string | null; readonly resolution: string }
  | { readonly type: "item.assigned"; readonly kind: ReviewKind; readonly item_id: string; readonly assignee_user_id: string | null }
  | { readonly type: "queue.changed"; readonly reason: string }
  | { readonly type: "thread.changed"; readonly kind: ReviewKind; readonly item_id: string; readonly thread_id: string; readonly action: string; readonly open_threads: number };

/* --------------------------------------------------------------- REST ---- */

export interface AnchorIn {
  readonly kind: AnchorKind;
  readonly page?: number;
  readonly paragraph?: number;
  readonly field?: string;
  readonly digest?: string;
}

export interface AnchorOut {
  readonly kind: AnchorKind;
  readonly page: number | null;
  readonly paragraph: number | null;
  readonly field: string | null;
  readonly quote: string | null;
  readonly digest: string | null;
  readonly state: AnchorState;
  readonly current_page: number | null;
  readonly current_paragraph: number | null;
}

export interface CommentOut {
  readonly id: string;
  readonly author: Person | null;
  readonly body: string | null;
  readonly deleted: boolean;
  readonly erased: boolean;
  readonly mentions: readonly Person[];
  readonly created_at: string;
  readonly edited_at: string | null;
}

export interface ThreadOut {
  readonly id: string;
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly status: ThreadStatus;
  readonly anchor: AnchorOut;
  readonly created_by: Person | null;
  readonly created_at: string;
  readonly last_activity_at: string;
  readonly resolved_at: string | null;
  readonly resolved_by: Person | null;
  readonly comments: readonly CommentOut[];
}

export interface ThreadList {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly threads: readonly ThreadOut[];
  readonly open_threads: number;
}

export interface NewThread {
  readonly anchor: AnchorIn;
  readonly body: string;
  readonly mentions: readonly string[];
  readonly client_nonce?: string;
}

export interface NewComment {
  readonly body: string;
  readonly mentions: readonly string[];
  readonly client_nonce?: string;
}

export interface EditComment {
  readonly body: string;
}

export interface ThreadCreated {
  readonly thread: ThreadOut;
  readonly created: boolean;
}

export interface CommentCreated {
  readonly thread: ThreadOut;
  readonly comment_id: string;
  readonly created: boolean;
}

export interface ParagraphOut {
  readonly index: number;
  readonly page: number;
  readonly text: string;
  readonly digest: string;
}

export interface ParagraphList {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly work_item_id: string | null;
  readonly paragraphs: readonly ParagraphOut[];
  readonly truncated: boolean;
}

export interface LockOut {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly holder_user_id: string;
  readonly holder: Person | null;
  readonly acquired_at: string;
  readonly expires_at: string;
}

export interface LiveState {
  readonly locks: readonly LockOut[];
  readonly presence: readonly Viewer[];
  readonly heartbeat_seconds: number;
  readonly lock_ttl_seconds: number;
  readonly subprotocol: string;
  readonly live_path: string;
}

export interface BreakLockResult {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly released: boolean;
  readonly holder_user_id: string | null;
}

export const ANCHOR_STATE_LABELS: Readonly<Record<AnchorState, string>> = {
  CURRENT: "",
  MOVED: "The paragraph moved after the document was reprocessed",
  OUTDATED: "This paragraph is no longer in the document",
};

/** Two letters for an avatar: initials of the name, else of the email. */
export const initials = (person: { readonly name?: string; readonly email?: string }): string => {
  const source = (person.name || person.email || "?").trim();
  const words = source.split(/[\s@._-]+/).filter(Boolean);
  const first = words[0]?.[0] ?? "?";
  const second = words.length > 1 ? words[1]?.[0] ?? "" : words[0]?.[1] ?? "";
  return `${first}${second}`.toUpperCase();
};

/** A stable hue per person, so the same reviewer has the same colour everywhere. */
export const personHue = (userId: string): number => {
  let hash = 0;
  for (let i = 0; i < userId.length; i += 1) {
    hash = (hash * 31 + userId.charCodeAt(i)) % 360;
  }
  return hash;
};
