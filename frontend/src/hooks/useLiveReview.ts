/**
 * ARCH48-S2:use-live-review. The review hub's live channel (Enterprise).
 *
 * One WebSocket per open review hub, authenticated with the SAME access token
 * the REST client sends (the browser cannot set an Authorization header on a
 * WebSocket, so the token rides in the subprotocol list next to
 * "flowpilot.review.v1"; the server never echoes it and the ingress log drops
 * the header). No new auth scheme, nothing stored.
 *
 * WHAT IT KEEPS
 *   presence   who is looking at which item (from every API worker: the server
 *              fans out through Redis)
 *   locks      the live soft locks, and the ones this tab holds
 *   status     off | connecting | live | reconnecting | refused
 *
 * WHAT IT DOES
 *   view(item)       tells the others which item this reviewer has open
 *   lock(item)       takes the soft lock before deciding (resolves with the
 *                    server's answer: ok, or LOCKED with the holder)
 *   unlock(item)     gives it back
 *   heartbeat        a ping every `heartbeat_seconds` (from the hello) extends
 *                    every lease this tab holds; a lost lease is reported
 *   reconnect        exponential backoff with jitter; after a reconnect the
 *                    view is re-announced and held locks re-taken (the server
 *                    hands the same lease back to the same person)
 *   4401             the access token expired or the session ended: a REST
 *                    call through the API client (which refreshes the token)
 *                    runs first, then the socket reconnects
 *   refused          a handshake the server refuses (plan, access, a token it
 *                    will not take) is an HTTP 403 to the browser; a REST probe
 *                    decides: 402 or 403 there -> the channel stays off (the
 *                    hub still works without it); 4403 on an open socket too
 *
 * Events that change the queue (item.resolved, item.assigned, queue.changed,
 * thread.changed) are handed to `onChange`, which the hub uses to invalidate
 * its queries: a resolution by someone else leaves everyone's queue at once.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getLiveState, liveUrl } from "@/services/api/collab";
import { ApiError } from "@/services/api/errors";
import { useAuthStore } from "@/store/useAuthStore";
import {
  CLOSE,
  LIVE_SUBPROTOCOL,
  LIVE_TOKEN_PREFIX,
  type LiveLock,
  type LockResultMessage,
  type Person,
  type ServerMessage,
  type Viewer,
} from "@/types/collab";
import type { ReviewKind } from "@/types/review";

export type LiveStatus = "off" | "connecting" | "live" | "reconnecting" | "refused";

export type LiveChange = Extract<
  ServerMessage,
  { type: "item.resolved" | "item.assigned" | "queue.changed" | "thread.changed" | "lock.released" }
>;

export interface LiveReview {
  readonly status: LiveStatus;
  readonly degraded: boolean;
  readonly me: Person | null;
  readonly viewers: readonly Viewer[];
  readonly locks: ReadonlyMap<string, LiveLock>;
  readonly held: ReadonlySet<string>;
  readonly viewersOf: (kind: ReviewKind, itemId: string) => readonly Viewer[];
  readonly lockOf: (kind: ReviewKind, itemId: string) => LiveLock | undefined;
  readonly view: (kind: ReviewKind | null, itemId: string | null) => void;
  readonly lock: (kind: ReviewKind, itemId: string) => Promise<LockResultMessage>;
  readonly unlock: (kind: ReviewKind, itemId: string) => void;
}

interface Options {
  readonly workspaceId: string;
  readonly enabled: boolean;
  readonly onChange?: (change: LiveChange) => void;
}

export const liveKey = (kind: string, itemId: string): string => `${kind}:${itemId}`;

const MAX_BACKOFF_MS = 30_000;
const LOCK_TIMEOUT_MS = 10_000;

const backoff = (attempt: number): number => {
  const ceiling = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(attempt, 5));
  return Math.round(ceiling / 2 + Math.random() * (ceiling / 2));
};

export const useLiveReview = ({ workspaceId, enabled, onChange }: Options): LiveReview => {
  const [status, setStatus] = useState<LiveStatus>("off");
  const [degraded, setDegraded] = useState(false);
  const [me, setMe] = useState<Person | null>(null);
  const [viewers, setViewers] = useState<readonly Viewer[]>([]);
  const [locks, setLocks] = useState<ReadonlyMap<string, LiveLock>>(new Map());
  const [held, setHeld] = useState<ReadonlySet<string>>(new Set());

  const socketRef = useRef<WebSocket | null>(null);
  const viewRef = useRef<{ kind: ReviewKind; item_id: string } | null>(null);
  const heldRef = useRef<Set<string>>(new Set());
  const pendingRef = useRef<Map<string, (result: LockResultMessage) => void>>(new Map());
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const send = useCallback((message: Record<string, unknown>): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(JSON.stringify(message));
    return true;
  }, []);

  useEffect(() => {
    if (!enabled || !workspaceId) {
      setStatus("off");
      return undefined;
    }
    let stopped = false;
    let attempt = 0;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const settlePending = (result: Omit<LockResultMessage, "kind" | "item_id" | "type">): void => {
      for (const [key, resolve] of pendingRef.current) {
        const [kind, itemId] = key.split(":");
        resolve({ type: "lock.result", kind: kind as ReviewKind, item_id: itemId ?? "", ...result });
      }
      pendingRef.current.clear();
    };

    const handle = (message: ServerMessage): void => {
      switch (message.type) {
        case "hello": {
          attempt = 0;
          setStatus("live");
          setMe(message.you);
          setDegraded(message.degraded);
          setViewers(message.presence);
          setLocks(new Map(message.locks.map((l) => [liveKey(l.kind, l.item_id), l])));
          if (heartbeat) {
            clearInterval(heartbeat);
          }
          heartbeat = setInterval(() => {
            send({ type: "ping" });
          }, Math.max(5, message.heartbeat_seconds) * 1000);
          if (viewRef.current) {
            send({ type: "view", ...viewRef.current });
          }
          for (const key of heldRef.current) {
            const [kind, itemId] = key.split(":");
            send({ type: "lock", kind, item_id: itemId });
          }
          break;
        }
        case "presence":
          setViewers(message.viewers);
          break;
        case "lock.acquired":
          setLocks((previous) => new Map(previous).set(liveKey(message.kind, message.item_id), message));
          break;
        case "lock.released": {
          const key = liveKey(message.kind, message.item_id);
          setLocks((previous) => {
            const next = new Map(previous);
            next.delete(key);
            return next;
          });
          if (heldRef.current.delete(key)) {
            setHeld(new Set(heldRef.current));
          }
          onChangeRef.current?.(message);
          break;
        }
        case "lock.result": {
          const key = liveKey(message.kind, message.item_id);
          if (message.ok && !message.released) {
            heldRef.current.add(key);
            setHeld(new Set(heldRef.current));
          }
          const resolve = pendingRef.current.get(key);
          pendingRef.current.delete(key);
          resolve?.(message);
          break;
        }
        case "pong": {
          if (message.lost.length) {
            for (const lost of message.lost) {
              heldRef.current.delete(liveKey(lost.kind, lost.item_id));
            }
            setHeld(new Set(heldRef.current));
          }
          break;
        }
        case "item.resolved":
        case "item.assigned":
        case "queue.changed":
        case "thread.changed":
          onChangeRef.current?.(message);
          break;
        default:
          break;
      }
    };

    const schedule = (delay: number): void => {
      if (stopped) {
        return;
      }
      setStatus("reconnecting");
      retry = setTimeout(connect, delay);
    };

    const connect = (): void => {
      if (stopped) {
        return;
      }
      const token = useAuthStore.getState().token;
      if (!token) {
        schedule(backoff(attempt++));
        return;
      }
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      let socket: WebSocket;
      try {
        socket = new WebSocket(liveUrl(workspaceId), [LIVE_SUBPROTOCOL, `${LIVE_TOKEN_PREFIX}${token}`]);
      } catch {
        schedule(backoff(attempt++));
        return;
      }
      socketRef.current = socket;
      let opened = false;
      socket.onopen = () => {
        opened = true;
      };
      socket.onmessage = (event: MessageEvent) => {
        try {
          const parsed = JSON.parse(String(event.data)) as ServerMessage;
          handle(parsed);
        } catch {
          // A frame this console does not understand is ignored, never fatal.
        }
      };
      socket.onclose = (event: CloseEvent) => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        if (heartbeat) {
          clearInterval(heartbeat);
          heartbeat = undefined;
        }
        settlePending({ ok: false, code: "DISCONNECTED", detail: "The live channel dropped. Try again." });
        if (stopped) {
          return;
        }
        if (event.code === CLOSE.ACCESS_LOST) {
          setStatus("refused");
          return;
        }
        if (event.code === CLOSE.SESSION_ENDED || !opened) {
          // A refused handshake reaches the browser as a failed connection (the server
          // answers the upgrade with HTTP 403 and no close code), indistinguishable from
          // a network error. A REST call through the API client tells them apart: it
          // refreshes an expired access token, or answers 402 when the plan does not
          // include live review (then the channel stays off). Otherwise: retry.
          getLiveState(workspaceId)
            .then(() => schedule(attempt++ === 0 ? 250 : backoff(attempt)))
            .catch((error: unknown) => {
              if (stopped) {
                return;
              }
              const status = error instanceof ApiError ? error.status : undefined;
              if (status === 402 || status === 403 || status === 404) {
                setStatus("refused");
                return;
              }
              // The network or the server is down: keep trying, slower each time.
              schedule(backoff(attempt++));
            });
          return;
        }
        if (event.code === CLOSE.RATE_LIMITED) {
          schedule(10_000);
          return;
        }
        schedule(backoff(attempt++));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (heartbeat) {
        clearInterval(heartbeat);
      }
      if (retry) {
        clearTimeout(retry);
      }
      const socket = socketRef.current;
      socketRef.current = null;
      // A clean close (1000): the server releases this tab's locks at once.
      socket?.close(1000);
      heldRef.current.clear();
      setHeld(new Set());
      setLocks(new Map());
      setViewers([]);
      setStatus("off");
    };
  }, [enabled, workspaceId, send]);

  const view = useCallback(
    (kind: ReviewKind | null, itemId: string | null): void => {
      const next = kind && itemId ? { kind, item_id: itemId } : null;
      const current = viewRef.current;
      if (current?.kind === next?.kind && current?.item_id === next?.item_id) {
        return;
      }
      viewRef.current = next;
      send(next ? { type: "view", ...next } : { type: "view" });
    },
    [send],
  );

  const lock = useCallback(
    (kind: ReviewKind, itemId: string): Promise<LockResultMessage> => {
      const key = liveKey(kind, itemId);
      return new Promise<LockResultMessage>((resolve) => {
        if (!send({ type: "lock", kind, item_id: itemId })) {
          resolve({ type: "lock.result", kind, item_id: itemId, ok: false, code: "OFFLINE", detail: "The live channel is not connected." });
          return;
        }
        pendingRef.current.get(key)?.({ type: "lock.result", kind, item_id: itemId, ok: false, code: "SUPERSEDED" });
        pendingRef.current.set(key, resolve);
        setTimeout(() => {
          const waiting = pendingRef.current.get(key);
          if (waiting === resolve) {
            pendingRef.current.delete(key);
            resolve({ type: "lock.result", kind, item_id: itemId, ok: false, code: "TIMEOUT", detail: "No answer from the live channel." });
          }
        }, LOCK_TIMEOUT_MS);
      });
    },
    [send],
  );

  const unlock = useCallback(
    (kind: ReviewKind, itemId: string): void => {
      const key = liveKey(kind, itemId);
      if (heldRef.current.delete(key)) {
        setHeld(new Set(heldRef.current));
        send({ type: "unlock", kind, item_id: itemId });
      }
    },
    [send],
  );

  const viewersOf = useCallback(
    (kind: ReviewKind, itemId: string): readonly Viewer[] => {
      const seen = new Set<string>();
      return viewers.filter((viewer) => {
        if (viewer.kind !== kind || viewer.item_id !== itemId || seen.has(viewer.user_id)) {
          return false;
        }
        seen.add(viewer.user_id);
        return true;
      });
    },
    [viewers],
  );

  const lockOf = useCallback(
    (kind: ReviewKind, itemId: string): LiveLock | undefined => locks.get(liveKey(kind, itemId)),
    [locks],
  );

  return useMemo(
    () => ({ status, degraded, me, viewers, locks, held, viewersOf, lockOf, view, lock, unlock }),
    [status, degraded, me, viewers, locks, held, viewersOf, lockOf, view, lock, unlock],
  );
};

export default useLiveReview;
