/**
 * Shared Axios instance and interceptors for the FlowPilot AI frontend.
 *
 * FE-0 extends the ARCH-03 client from "401 with one refresh" to the full
 * status taxonomy. The refresh mutex below is unchanged in behaviour and
 * deliberately so — it is correct, it is load-bearing, and FE-0 is not a
 * reason to rewrite it.
 *
 * WHAT FE-0 ADDS, AND WHY EACH STATUS GETS ITS OWN PATH
 * =====================================================
 *
 * The pre-FE-0 client had two outcomes: refresh-and-retry on 401, or reject.
 * Every other failure reached the UI as an `ApiError` with a status number
 * attached, which meant each call site decided for itself what a 402 meant.
 * They decided differently, and two of them retried.
 *
 * A retry is right for exactly one of these statuses:
 *
 *   401  the credential expired          → refresh once, replay once
 *   402  the tenant is out of quota      → NEVER retry; a ceiling is not transient
 *   403  permission, or stale auth_time  → challenge if step-up, else surface
 *   404  absent *or* not yours           → surface; the guard decides, not this file
 *   429  the limiter refused             → wait out Retry-After, then replay once
 *
 * 402 is the one worth stating plainly. `SpendLimitExceededError` means the
 * workspace has hit its monthly ceiling. Retrying produces an identical
 * refusal, and a component that retries on failure will do so until the tab is
 * closed — spending a request quota against an endpoint that has already said
 * no. It is raised to the UI once, held there, and cleared only by a human.
 *
 * A NOTE ON HEADER VISIBILITY (READ BEFORE CHANGING THE 403/429 PATHS)
 * ===================================================================
 *
 * Two branches below want to read a response header:
 *
 *   403  `WWW-Authenticate: Bearer error="reauth_required"`  (billing.py)
 *   429  `Retry-After: <delta-seconds>`                      (global_rate_limit.py)
 *
 * Neither is CORS-safelisted, and `app/main.py` configures `CORSMiddleware`
 * without `expose_headers`. Cross-origin — which is every deployment, since
 * `app.` and `api.` are different origins, and also localhost:3000 against
 * localhost:8000 — `error.response.headers` will not contain them. The browser
 * receives the headers and refuses to hand them to script.
 *
 * So both branches read the header **when it is there** and fall back to the
 * response body **when it is not**. The fallbacks are not guesses; each is
 * pinned to a specific backend construct and cited at the call site. The
 * header path is the one that should win once the backend adds:
 *
 *     expose_headers=["WWW-Authenticate", "Retry-After"]
 *
 * Until then the fallback is doing the work, and deleting it would silently
 * disable the step-up modal in production while it kept working through a
 * same-origin dev proxy.
 */

import axios from "axios";

import type {
  AxiosError,
  AxiosRequestConfig,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";

import { useAuthStore } from "@/store/useAuthStore";
import { useSessionGuardStore } from "@/store/useSessionGuardStore";
import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ApiError, parseErrorEnvelope } from "@/services/api/errors";
import { buildLoginRedirect } from "@/utils/security";

export { ApiError } from "@/services/api/errors";
export type { ParsedApiError } from "@/services/api/errors";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

/**
 * Routes that must never trigger a refresh attempt.
 *
 * /auth/refresh 401ing is the terminal answer, not a prompt to try again —
 * retrying it would recurse until the stack gave out. login and logout are
 * excluded because a 401 from them means what it says.
 */
const NO_REFRESH_PATHS = ["/auth/refresh", "/auth/login", "/auth/logout"];

const isRefreshExempt = (url: string | undefined): boolean =>
  !!url && NO_REFRESH_PATHS.some((path) => url.includes(path));

/**
 * Per-request bookkeeping.
 *
 * Two independent flags rather than one counter: a request may legitimately be
 * replayed once for a refreshed token *and* once for an expired rate limit,
 * and collapsing them into a single `_retried` would let the second cause
 * swallow the first. Neither may fire twice.
 */
interface RetriableConfig extends InternalAxiosRequestConfig {
  _refreshRetried?: boolean;
  _rateLimitRetried?: boolean;

  /**
   * Opts a request out of the global step-up modal.
   *
   * Set by callers that intend to handle a 403 themselves — the step-up modal
   * replaying its own re-auth, most importantly, which would otherwise
   * challenge in response to the challenge.
   */
  _skipStepUp?: boolean;
}

/** Public per-request options, merged into the axios config by callers. */
export interface RequestOptions extends AxiosRequestConfig {
  _skipStepUp?: boolean;
}

export const apiClient = axios.create({
  baseURL: API_URL,
  timeout: 15000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig): InternalAxiosRequestConfig => {
    const token = useAuthStore.getState().token;

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },

  (error: AxiosError) =>
    Promise.reject(
      new ApiError(
        error.message || "Failed to prepare request.",
        undefined,
        API_ERROR_CODES.NETWORK_ERROR,
      ),
    ),
);

/* ==========================================================================
 * Single-flight refresh
 * ========================================================================== */

/**
 * In-flight refresh, shared by every request that hits a 401 at once.
 *
 * A page that fires six parallel requests will get six 401s the moment the
 * access token expires. Without this, each would POST /auth/refresh: the first
 * rotates the token and the other five present one that has just been
 * superseded. The backend's grace window absorbs that, but it means five
 * needless rotations and five extra rows per expiry — and if the window were
 * ever shortened, five reuse alerts on a completely ordinary page load.
 *
 * One promise, awaited by all of them, one rotation.
 */
let refreshInFlight: Promise<string> | null = null;

const performRefresh = async (): Promise<string> => {
  // A bare axios call, not apiClient. Going through the instance would re-enter
  // these interceptors and, on failure, attempt to refresh the refresh.
  const response = await axios.post<{ access_token: string }>(
    `${API_URL}/auth/refresh`,
    null,
    { withCredentials: true, timeout: 15000 },
  );
  return response.data.access_token;
};

const refreshOnce = (): Promise<string> => {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
};

/* ==========================================================================
 * Terminal session end
 * ========================================================================== */

/**
 * Ends the session and sends the browser to the login screen.
 *
 * The destination is built by `buildLoginRedirect`, which validates the
 * current path before embedding it. That validation is not ceremony: the value
 * comes back out of the URL after login and is handed to `navigate()`, so an
 * unvalidated round trip is an open redirect with extra steps.
 *
 * `assign` rather than `href = serverValue`: the string is composed here from
 * a literal and an encoded same-origin path, and no part of it originates in a
 * response body. Nothing the server sends can steer this navigation.
 */
const endSession = (): void => {
  useAuthStore.getState().clearAuth();
  useSessionGuardStore.getState().resetSessionGuards();

  if (typeof window === "undefined") {
    return;
  }

  const current = `${window.location.pathname}${window.location.search}`;
  const destination = buildLoginRedirect(current);

  // Already there. Navigating again would loop and would discard the redirect
  // captured by whichever request failed first.
  if (window.location.pathname === "/login") {
    return;
  }

  window.location.assign(destination);
};

/* ==========================================================================
 * Step-up detection
 * ========================================================================== */

/**
 * RFC 6750 form emitted by `portal_service` via `billing.py`.
 *
 * Matched loosely on the error token rather than the whole string: the scheme
 * and parameter order are not guaranteed, and a strict equality check would
 * break the first time someone appended a `realm`.
 */
const REAUTH_HEADER_PATTERN = /error\s*=\s*"?reauth_required"?/i;

/**
 * True when a 401/403 is asking for fresh credentials rather than refusing
 * outright.
 *
 * Three signals, checked in order of authority:
 *
 * 1. `WWW-Authenticate: Bearer error="reauth_required"`. The real contract,
 *    from `app/api/v1/billing.py`. Invisible cross-origin until the backend
 *    sets `expose_headers` — see the module header.
 *
 * 2. `code === "REAUTHENTICATION_FAILED"`. The domain envelope for
 *    `ReauthenticationFailedError`, mapped at
 *    `app/core/exception_handlers.py`. Always readable, since it is in the
 *    body. This is what `ownership_transfer_service` raises.
 *
 * 3. `details.reason === "reauthentication_required"`. Present on the audit
 *    record for the billing path and cheap to check.
 *
 * A body-message substring match was considered as a fourth signal and
 * rejected. `message` is display prose the backend rewords freely; branching
 * on it would make a copy edit into a production auth regression.
 */
const isStepUpChallenge = (
  response: AxiosResponse<unknown>,
  code: string | undefined,
  details: Record<string, unknown>,
): boolean => {
  const header =
    response.headers?.["www-authenticate"] ??
    (response.headers as Record<string, unknown> | undefined)?.[
      "WWW-Authenticate"
    ];

  if (typeof header === "string" && REAUTH_HEADER_PATTERN.test(header)) {
    return true;
  }

  if (code === API_ERROR_CODES.REAUTHENTICATION_FAILED) {
    return true;
  }

  return details.reason === "reauthentication_required";
};

/* ==========================================================================
 * Retry-After resolution
 * ========================================================================== */

/** Ceiling on any server-suggested wait, so a bad value cannot hang the UI. */
const MAX_BACKOFF_MS = 120_000;
const DEFAULT_BACKOFF_MS = 5_000;

/**
 * Resolves how long to wait before replaying a 429.
 *
 * `Retry-After` is RFC 9110, which permits both delta-seconds and an
 * HTTP-date. `global_rate_limit.py` emits delta-seconds, but
 * `webhook_dispatch.py` already parses both on the way in, so the client
 * accepts both on the way out rather than assuming the one it happens to see.
 *
 * When the header is unreadable — which cross-origin is the normal case, per
 * the module header — the body is consulted. `RateLimitExceededError` carries
 * `retry_after`, and the SSE `error` frame for a rate-limited generation
 * includes it too.
 *
 * The fallback is a fixed delay, not an escalating one. Escalation belongs to
 * the caller that decides to keep trying; this function answers a narrower
 * question: how long did the server say to wait.
 */
const resolveRetryAfterMs = (
  response: AxiosResponse<unknown>,
  details: Record<string, unknown>,
): number => {
  const header =
    response.headers?.["retry-after"] ??
    (response.headers as Record<string, unknown> | undefined)?.["Retry-After"];

  if (typeof header === "string" && header.trim().length > 0) {
    const seconds = Number(header);

    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.min(seconds * 1000, MAX_BACKOFF_MS);
    }

    const asDate = Date.parse(header);
    if (Number.isFinite(asDate)) {
      return Math.min(Math.max(asDate - Date.now(), 0), MAX_BACKOFF_MS);
    }
  }

  const fromBody = details.retry_after;
  if (typeof fromBody === "number" && Number.isFinite(fromBody)) {
    return Math.min(Math.max(fromBody, 0) * 1000, MAX_BACKOFF_MS);
  }

  return DEFAULT_BACKOFF_MS;
};

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/* ==========================================================================
 * Response interceptor
 * ========================================================================== */

apiClient.interceptors.response.use(
  (response) => {
    // A successful response is proof the limiter and the ceiling are no longer
    // in force. Clearing here means the banners disappear when the situation
    // resolves rather than when the user reloads.
    const guards = useSessionGuardStore.getState();
    if (guards.rateLimit !== null) {
      guards.clearRateLimit();
    }
    return response;
  },

  async (error: AxiosError<unknown>) => {
    if (!error.response) {
      return Promise.reject(
        new ApiError(
          "Unable to reach the server.",
          undefined,
          API_ERROR_CODES.NETWORK_ERROR,
        ),
      );
    }

    const response = error.response;
    const status = response.status;
    const config = error.config as RetriableConfig | undefined;

    const parsed = parseErrorEnvelope(
      response.data,
      status,
      error.message || "An unexpected server error occurred.",
    );

    const reject = (message?: string): Promise<never> =>
      Promise.reject(
        new ApiError(
          message ?? parsed.message,
          status,
          parsed.code,
          parsed.message,
          parsed.details,
        ),
      );

    /* ---------------------------------------------------------------- 401 */

    if (status === 401) {
      // A 401 that is really a step-up request. `ReauthenticationFailedError`
      // maps to 401, not 403, so this check must run before the refresh path —
      // refreshing would succeed, replay, and get refused identically, because
      // refresh carries `auth_time` forward rather than restamping it.
      if (
        config &&
        !config._skipStepUp &&
        isStepUpChallenge(response, parsed.code, parsed.details)
      ) {
        useSessionGuardStore.getState().requireStepUp({
          reason: parsed.message,
          retry: null,
          resourcePath: config.url,
        });
        return reject();
      }

      if (config && !config._refreshRetried && !isRefreshExempt(config.url)) {
        config._refreshRetried = true;

        try {
          const token = await refreshOnce();
          useAuthStore.getState().setToken(token);
          config.headers.Authorization = `Bearer ${token}`;
          return await apiClient(config as AxiosRequestConfig);
        } catch {
          // The refresh cookie is gone, expired, or was revoked — including the
          // reuse-detection case, where the server has already signed this
          // device out on purpose. Nothing further to try.
          endSession();

          return reject("Session expired. Please sign in again.");
        }
      }

      endSession();
      return reject("Session expired. Please sign in again.");
    }

    /* ---------------------------------------------------------------- 402 */

    if (status === 402) {
      // Held, not retried. See the module header.
      //
      // Two shapes arrive here and both are accepted: the domain envelope from
      // `SpendLimitExceededError` (code `SPEND_LIMIT_EXCEEDED`) and the bare
      // `HTTPException` that `assistant_stream.py` raises before it can return
      // a StreamingResponse. The second has no code, which is why `code` is
      // optional on `QuotaRefusal`.
      useSessionGuardStore.getState().setQuotaRefusal({
        code: parsed.code,
        message: parsed.message,
        observedAt: Date.now(),
      });

      return reject();
    }

    /* ---------------------------------------------------------------- 403 */

    if (status === 403) {
      if (
        config &&
        !config._skipStepUp &&
        isStepUpChallenge(response, parsed.code, parsed.details)
      ) {
        useSessionGuardStore.getState().requireStepUp({
          reason: parsed.message,
          // Replayed verbatim after the challenge succeeds. The config carries
          // its own `_refreshRetried` state, so a replay that 401s still gets
          // its one refresh.
          retry: () => {
            void apiClient(config as AxiosRequestConfig);
          },
          resourcePath: config.url,
        });

        return reject();
      }

      // An ordinary permission refusal — `PERMISSION_DENIED`,
      // `TENANT_SUSPENDED`, `INVITATION_EMAIL_MISMATCH`. Surfaced with its code
      // intact so the caller can render a tombstone that names the reason. No
      // global UI: what a 403 means depends entirely on what was being done.
      return reject();
    }

    /* ---------------------------------------------------------------- 404 */

    if (status === 404) {
      // Deliberately unhandled here, and that is the anti-enumeration
      // discipline working as designed.
      //
      // `app/core/exception_handlers.py` maps both `OrganizationNotFoundError`
      // and `OrganizationAccessDeniedError` to 404 / `RESOURCE_NOT_FOUND` — the
      // same status and the same code — so that a caller cannot tell "no such
      // tenant" from "not yours". A client that reacted differently to the two
      // would rebuild the oracle the backend just removed.
      //
      // Routing to a tenant picker is therefore a *route guard* decision made
      // from the shape of the URL, not an interceptor decision made from the
      // response. `TenantGuard` owns it.
      return reject();
    }

    /* ---------------------------------------------------------------- 429 */

    if (status === 429) {
      const waitMs = resolveRetryAfterMs(response, parsed.details);

      useSessionGuardStore.getState().setRateLimit({
        retryAt: Date.now() + waitMs,
        scope:
          typeof parsed.details.scope === "string"
            ? parsed.details.scope
            : undefined,
      });

      // One automatic replay, and only for idempotent verbs.
      //
      // Replaying a POST that the limiter refused is usually safe, because a
      // refused request did not execute — but "usually" is not a property to
      // build a billing client on. `POST /assistant/.../messages/stream` is
      // the exact case: A13 in the roadmap is about a retry becoming a second
      // generation and a second charge. GET/HEAD/OPTIONS only.
      const method = (config?.method ?? "get").toLowerCase();
      const isIdempotent = ["get", "head", "options"].includes(method);

      if (
        config &&
        isIdempotent &&
        !config._rateLimitRetried &&
        waitMs <= 30_000
      ) {
        config._rateLimitRetried = true;
        await delay(waitMs);
        return apiClient(config as AxiosRequestConfig);
      }

      return reject();
    }

    /* ------------------------------------------------------------ default */

    return reject();
  },
);

/* ==========================================================================
 * Session restoration
 * ========================================================================== */

/**
 * Restores a session from the refresh cookie on application start.
 *
 * The access token no longer survives a reload — it lives in memory only. What
 * survives is the HttpOnly cookie, which the browser will present here. A
 * successful call means this browser still holds a valid session and the user
 * should not see a login screen.
 *
 * Returns the access token, or null if there is no session to restore. Never
 * throws: "not signed in" is an ordinary answer at startup, not an error.
 */
export const restoreSession = async (): Promise<string | null> => {
  try {
    const token = await refreshOnce();
    useAuthStore.getState().setToken(token);
    return token;
  } catch {
    useAuthStore.getState().clearAuth();
    useSessionGuardStore.getState().resetSessionGuards();
    return null;
  }
};

export default apiClient;
