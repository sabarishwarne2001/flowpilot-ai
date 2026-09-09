/**
 * Polling intervals that stop when the server has said no.
 *
 * THE PROBLEM THIS SOLVES
 *
 * `retry` and `refetchInterval` are separate mechanisms in TanStack Query and
 * only the first one was guarded. main.tsx already refuses to retry a 401,
 * 403 or 404 — correctly — but a fixed `refetchInterval` is not a retry. It
 * fires on a timer regardless of what the last attempt returned.
 *
 * So a component polling every 5 seconds against a tenant it cannot read sent
 * 12 requests a minute, forever, each one answered 403. Two such pollers were
 * mounted at once (notifications at 5s, automation logs at 5s), which is 24
 * requests a minute per tab from components nobody was looking at. That is
 * what tripped the rate limiter, and the 429 then looked like the problem
 * rather than the symptom.
 *
 * The distinction matters when reading the code: retrying a failure is asking
 * the same question again because the answer might have been noise. Polling
 * is asking a NEW question on a schedule. A 403 answers every future question
 * as well as the one that was asked, so the schedule should stop — and 401,
 * 403 and 404 are all statements about the request itself, not about the
 * moment it arrived.
 *
 * 429 IS retried on schedule, deliberately. A limiter refusal is transient by
 * definition, the axios layer already honours Retry-After, and stopping a
 * poller permanently because of one busy moment would leave a tab silently
 * stale until a reload.
 *
 * WHY THIS IS NOT A GLOBAL DEFAULT
 *
 * A global `refetchInterval` in main.tsx would put every query on a timer,
 * including the hundreds that should fetch once. This is opt-in: a component
 * that wants polling asks for it and gets the stop condition with it.
 */

import { ApiError } from "@/services/api/client";

/**
 * Statuses that make a repeat request pointless.
 *
 * 401 — the session is gone. Polling cannot restore it; the interceptor's
 *       refresh path or a redirect to login will.
 * 403 — this actor may not read this resource. True until a membership or a
 *       tenant status changes, neither of which a poll can cause.
 * 404 — the resource does not exist. A workspace deleted in another tab, or a
 *       tenant archived while this one was open.
 */
const TERMINAL_STATUSES: ReadonlySet<number> = new Set([401, 403, 404]);

export const isTerminalQueryError = (error: unknown): boolean =>
  error instanceof ApiError &&
  typeof error.status === "number" &&
  TERMINAL_STATUSES.has(error.status);

/**
 * A `refetchInterval` that halts after a terminal error.
 *
 *     refetchInterval: pollUnlessRefused(5_000),
 *
 * Returning `false` stops the timer. It restarts on the next successful fetch
 * — a manual refetch, a window focus, or a remount — so recovery does not
 * need a reload once access is restored.
 */
export const pollUnlessRefused =
  (intervalMs: number) =>
  // Structurally typed rather than taking TanStack's `Query<...>`, which is
  // generic over four parameters and contravariant in the callback position:
  // a concrete `Query<BillingAccessResponse, ...>` is not assignable to a
  // callback declared over `Query<unknown, ...>`, so a nominal signature
  // fails to compile at every call site with a typed queryFn. The only thing
  // read here is the last error, so that is all this asks for.
  (query: { readonly state: { readonly error: unknown } }): number | false =>
    isTerminalQueryError(query.state.error) ? false : intervalMs;
