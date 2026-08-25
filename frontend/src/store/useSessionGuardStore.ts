import { create } from "zustand";
import { devtools } from "zustand/middleware";

/**
 * Cross-cutting session state that the axios interceptor raises and React
 * renders.
 *
 * WHY THIS IS A STORE AND NOT CONTEXT
 * ===================================
 *
 * The interceptor is not a component. It has no hooks, no provider above it,
 * and it fires during a promise rejection that may not correspond to any
 * mounted tree. It needs a way to say "the server asked for a step-up" that
 * works from module scope, and `useSessionGuardStore.getState()` is that way.
 *
 * The alternative — throwing a typed error and expecting every call site to
 * catch it and open a modal — was rejected because it puts the same six-line
 * handler in front of every mutation in the application, and the one place
 * that forgets is the one place that silently fails closed with no UI.
 *
 * NOTHING HERE IS A CREDENTIAL
 * ============================
 *
 * This store is deliberately not persisted. Everything in it describes the
 * current tab's relationship to the server right now: a pending challenge, a
 * quota the server refused against, a backoff deadline. All three are
 * re-derived from the next response, and persisting them would mean a reload
 * restores a challenge the server has already forgotten about.
 */

/* ==========================================================================
 * Step-up re-authentication
 * ========================================================================== */

/**
 * A pending step-up challenge.
 *
 * GROUNDING NOTE — the trigger is not a custom header.
 *
 * `app/api/v1/billing.py` raises 403 with
 * `WWW-Authenticate: Bearer error="reauth_required"`, which is the RFC 6750
 * form. There is no `X-Reauth-Required` header anywhere in the backend, so
 * nothing may branch on one.
 *
 * The corresponding *domain* signal is `ReauthenticationFailedError`, which
 * `app/core/exception_handlers.py` maps to 401 / `REAUTHENTICATION_FAILED`.
 * Both are represented here because they mean the same thing to a user and
 * arrive by different routes.
 */
export interface StepUpChallenge {
  /** Human-readable reason, taken from the server's message. */
  readonly reason: string;

  /**
   * The request that was refused, so it can be replayed after the challenge
   * is satisfied. Held as a thunk rather than an axios config: replaying
   * through the original call site preserves its own error handling.
   */
  readonly retry: (() => void) | null;

  /** Path the challenge originated from, for the audit trail and copy. */
  readonly resourcePath: string | undefined;
}

/* ==========================================================================
 * Quota exhaustion
 * ========================================================================== */

/**
 * A 402 refusal, held until the user acts on it.
 *
 * Distinct from a transient error because it is not transient: the workspace
 * has hit a ceiling and every subsequent request of the same kind will be
 * refused identically until a human raises the limit or the period rolls over.
 * Retrying is not merely useless, it is the loop the roadmap's hardening
 * invariant forbids.
 */
export interface QuotaRefusal {
  /**
   * Stable branching code from the domain envelope — `SPEND_LIMIT_EXCEEDED`
   * for `SpendLimitExceededError`. Absent when the refusal arrived as a bare
   * `HTTPException`, which is how `assistant_stream.py` raises its 402.
   */
  readonly code: string | undefined;

  /** Server-authored prose. Displayed verbatim; never parsed. */
  readonly message: string;

  /** When this refusal was observed, for "still blocked" copy. */
  readonly observedAt: number;
}

/* ==========================================================================
 * Rate limiting
 * ========================================================================== */

/**
 * An active 429 backoff.
 *
 * `GlobalRateLimitMiddleware` emits `Retry-After` in delta-seconds, and
 * `RateLimitExceededError` carries `retry_after` through
 * `FlowPilotError.response_headers`. The deadline is stored as an absolute
 * timestamp rather than a remaining count so that a countdown can be rendered
 * without the store ticking — the component owns the interval, the store owns
 * the fact.
 */
export interface RateLimitState {
  /** Epoch milliseconds at which requests may resume. */
  readonly retryAt: number;

  /** Scope the limiter refused, when the server names one. */
  readonly scope: string | undefined;
}

/* ==========================================================================
 * Store
 * ========================================================================== */

interface SessionGuardState {
  readonly stepUp: StepUpChallenge | null;
  readonly quota: QuotaRefusal | null;
  readonly rateLimit: RateLimitState | null;

  /**
   * Raises a step-up challenge.
   *
   * Ignores the call when a challenge is already open. Six parallel requests
   * against a stale `auth_time` produce six 403s, and the user must be asked
   * once — not six times, and not have the first challenge's retry thunk
   * replaced by the last one to arrive.
   */
  readonly requireStepUp: (challenge: StepUpChallenge) => void;

  /**
   * Clears the challenge and returns the retry thunk, if any.
   *
   * Returns rather than invokes so the caller decides when to replay — the
   * modal needs to close first, or the replayed request's own error handling
   * renders behind a modal that is still mounted.
   */
  readonly resolveStepUp: () => (() => void) | null;

  /** Dismisses the challenge without satisfying it. Fails closed: no replay. */
  readonly cancelStepUp: () => void;

  readonly setQuotaRefusal: (refusal: QuotaRefusal) => void;
  readonly clearQuotaRefusal: () => void;

  readonly setRateLimit: (state: RateLimitState) => void;
  readonly clearRateLimit: () => void;

  /** Drops everything. Called on sign-out and on tenant switch. */
  readonly resetSessionGuards: () => void;
}

export const useSessionGuardStore = create<SessionGuardState>()(
  devtools(
    (set, get) => ({
      stepUp: null,
      quota: null,
      rateLimit: null,

      requireStepUp: (challenge) => {
        if (get().stepUp !== null) {
          return;
        }
        set({ stepUp: challenge }, false, "requireStepUp");
      },

      resolveStepUp: () => {
        const pending = get().stepUp;
        set({ stepUp: null }, false, "resolveStepUp");
        return pending?.retry ?? null;
      },

      cancelStepUp: () => set({ stepUp: null }, false, "cancelStepUp"),

      setQuotaRefusal: (refusal) =>
        set({ quota: refusal }, false, "setQuotaRefusal"),

      clearQuotaRefusal: () => set({ quota: null }, false, "clearQuotaRefusal"),

      setRateLimit: (state) => {
        // Keep the later deadline. Two concurrent 429s with different
        // Retry-After values must not let the shorter one release the UI
        // while the longer limit is still in force.
        const existing = get().rateLimit;
        if (existing && existing.retryAt > state.retryAt) {
          return;
        }
        set({ rateLimit: state }, false, "setRateLimit");
      },

      clearRateLimit: () => set({ rateLimit: null }, false, "clearRateLimit"),

      resetSessionGuards: () =>
        set(
          { stepUp: null, quota: null, rateLimit: null },
          false,
          "resetSessionGuards",
        ),
    }),
    { name: "SessionGuardStore" },
  ),
);
