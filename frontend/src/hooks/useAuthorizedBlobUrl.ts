/**
 * ARCH41-S1:authorized-blob-url — fetch an authenticated binary through the API
 * client and expose it as an object URL that this hook alone owns.
 *
 * WHY NOT `useAuthenticatedImage`
 * ===============================
 *
 * That hook serves avatars and logos, and its module-level LRU cache is right
 * for them. It is wrong for the Redaction Studio on three counts:
 *
 *   1. It keys by URL. The burned preview for a page has the same URL before
 *      and after a region is toggled, so a cache hit would show the result of
 *      a redaction set that no longer exists — on the one screen whose job is
 *      deciding what gets destroyed.
 *   2. The page preview is a rendering of the UNREDACTED document and the
 *      endpoint sends `Cache-Control: no-store` for exactly that reason. A
 *      session-lifetime cache of those bytes would undo the header in the
 *      browser that asked for it.
 *   3. The cache holds 64 entries and revokes on eviction. A reviewer paging
 *      through a long document would evict — and revoke — an object URL that
 *      is still on screen.
 *
 * WHAT THIS HOOK GUARANTEES
 * =========================
 *
 *   * The request goes through `apiClient`, so it carries the session and the
 *     API base URL. A bare `<img src="/workspaces/...">` has neither: it asks
 *     the SPA origin for the path and receives `index.html`.
 *   * A new path aborts the request for the old one. Page previews are
 *     rendered server-side by PDFium; flicking through ten pages must not
 *     leave nine renders running on the worker for images nobody will see.
 *   * The previous object URL stays on screen while the next one loads, so an
 *     overlay positioned over the page does not jump to an empty box.
 *   * Every object URL is revoked when it is replaced and on unmount. Nothing
 *     is cached; nothing outlives the component.
 */
import { useEffect, useState } from "react";

import apiClient from "@/services/api/client";
import { ApiError } from "@/services/api/errors";

export type AuthorizedBlobStatus = "idle" | "loading" | "ready" | "error";

export interface AuthorizedBlob {
  /** The most recent successfully loaded object URL, kept while the next loads. */
  readonly url: string | null;
  readonly status: AuthorizedBlobStatus;
  /** A sentence for the user when `status` is "error". */
  readonly error: string | null;
}

const FALLBACK_MESSAGE = "The page preview could not be loaded.";

const IDLE: AuthorizedBlob = { url: null, status: "idle", error: null };

/**
 * The API client's error envelope is JSON, but with `responseType: "blob"`
 * the body of a 402/404/422 arrives as a Blob. Read it back so the message the
 * server wrote is the one the reviewer sees.
 */
async function describeFailure(caught: unknown): Promise<string> {
  const response = (caught as { response?: { data?: unknown } } | null)?.response;
  const data = response?.data;
  if (data instanceof Blob) {
    try {
      const body = JSON.parse(await data.text()) as {
        message?: unknown;
        detail?: unknown;
      };
      const text = body.message ?? body.detail;
      if (typeof text === "string" && text.trim()) {
        return text;
      }
    } catch {
      // Not JSON. Fall through to the client's own reading of the error.
    }
  }
  if (caught instanceof ApiError && caught.message) {
    return caught.message;
  }
  return FALLBACK_MESSAGE;
}

function isAbort(caught: unknown): boolean {
  const name = (caught as { name?: unknown; code?: unknown } | null)?.name;
  const code = (caught as { code?: unknown } | null)?.code;
  return name === "CanceledError" || name === "AbortError" || code === "ERR_CANCELED";
}

/**
 * @param path An API-relative path such as `/workspaces/{id}/...`, or null to
 *   load nothing. A leading `/api/v1` is tolerated and stripped, matching
 *   `useAuthenticatedImage`, because `apiClient` already carries the base.
 */
export function useAuthorizedBlobUrl(path: string | null): AuthorizedBlob {
  const [state, setState] = useState<AuthorizedBlob>(IDLE);

  useEffect(() => {
    if (!path) {
      setState(IDLE);
      return;
    }

    const controller = new AbortController();
    setState((previous) => ({ url: previous.url, status: "loading", error: null }));

    apiClient
      .get(path.replace(/^\/api\/v1/, ""), {
        responseType: "blob",
        signal: controller.signal,
      })
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        const data: unknown = response.data;
        const blob =
          data instanceof Blob ? data : new Blob([data as BlobPart], { type: "image/png" });
        if (blob.size === 0) {
          setState((previous) => ({
            url: previous.url,
            status: "error",
            error: FALLBACK_MESSAGE,
          }));
          return;
        }
        setState({ url: URL.createObjectURL(blob), status: "ready", error: null });
      })
      .catch(async (caught: unknown) => {
        if (controller.signal.aborted || isAbort(caught)) {
          return;
        }
        const message = await describeFailure(caught);
        setState((previous) => ({ url: previous.url, status: "error", error: message }));
      });

    return () => {
      controller.abort();
    };
  }, [path]);

  // Revoke each object URL once it has been replaced (the cleanup runs after
  // the render that shows its successor commits) and on unmount. The initial
  // value is null, so React StrictMode's mount/unmount/mount replay has
  // nothing to revoke.
  const current = state.url;
  useEffect(
    () => () => {
      if (current) {
        URL.revokeObjectURL(current);
      }
    },
    [current],
  );

  return state;
}

export default useAuthorizedBlobUrl;
