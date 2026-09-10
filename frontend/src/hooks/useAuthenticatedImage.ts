/**
 * Fetch an authenticated image through the API client and expose it as an
 * object URL.
 *
 * ARCH-07 Step 7.
 */
import { useEffect, useRef, useState } from "react";
import apiClient from "@/services/api/client";

/**
 * ARCH-29 Tranche 3 — module-level blob cache.
 *
 * THE PROBLEM
 * ===========
 *
 * Every mount refetched. On a page reload the sidebar avatar was blank for
 * 4-6 seconds while the blob came back over the network, and navigating between
 * routes that both render an avatar refetched the identical bytes.
 *
 * The `Cache-Control: no-cache` / `Pragma: no-cache` headers below are why the
 * browser cache does not help: they were added so a changed avatar would not
 * serve stale, and they work — at the cost of a guaranteed network round trip
 * on every single mount. This cache restores the fast path without restoring
 * the staleness, because invalidation now happens through the `?v=` parameter
 * (see `useAvatarVersionStore`), which changes the KEY rather than needing the
 * cache to be bypassed.
 *
 * WHY OBJECT URLS ARE NEVER REVOKED WHILE CACHED
 * ==============================================
 *
 * The previous implementation revoked on unmount. That is correct for an
 * uncached URL and fatal for a cached one: the second component to mount would
 * receive a revoked URL and render a broken image. Cached entries are owned by
 * this module and outlive every component; the memory is one small image per
 * distinct URL, bounded by MAX_ENTRIES.
 *
 * The cache is per-session and per-tab. It holds authenticated image bytes, so
 * it must never be moved to localStorage — that would persist another user's
 * avatar on a shared machine past logout.
 */
const MAX_ENTRIES = 64;
const blobCache = new Map<string, string>();

function cacheGet(key: string): string | undefined {
  const hit = blobCache.get(key);
  if (hit !== undefined) {
    // Re-insert to make this the most-recently-used entry.
    blobCache.delete(key);
    blobCache.set(key, hit);
  }
  return hit;
}

function cacheSet(key: string, objectUrl: string): void {
  if (blobCache.size >= MAX_ENTRIES) {
    const oldestKey = blobCache.keys().next().value;
    if (oldestKey !== undefined) {
      const evicted = blobCache.get(oldestKey);
      blobCache.delete(oldestKey);
      if (evicted) {
        URL.revokeObjectURL(evicted);
      }
    }
  }
  blobCache.set(key, objectUrl);
}

/** Drop every cached image. Call on logout — these are authenticated bytes. */
export function clearAuthenticatedImageCache(): void {
  for (const objectUrl of blobCache.values()) {
    URL.revokeObjectURL(objectUrl);
  }
  blobCache.clear();
}

export function useAuthenticatedImage(url: string | null): string | null {
  // Seeded from the cache so a cached image is present on the FIRST render.
  // Initialising to null and setting in an effect would still blank for one
  // frame, which on a sidebar avatar is a visible flicker on every navigation.
  const [objectUrl, setObjectUrl] = useState<string | null>(() =>
    url ? cacheGet(url) ?? null : null,
  );
  const activeBlobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!url) {
      setObjectUrl(null);
      return;
    }

    const cached = cacheGet(url);
    if (cached !== undefined) {
      setObjectUrl(cached);
      return;
    }

    if (url.startsWith("blob:") || url.startsWith("data:")) {
      setObjectUrl(url);
      return;
    }

    let active = true;
    const normalizedUrl = url.replace(/^\/api\/v1/, "");

    apiClient
      .get(normalizedUrl, {
        responseType: "blob",
        headers: {
          "Cache-Control": "no-cache",
          Pragma: "no-cache",
        },
      })
      .then((response) => {
        if (!active) {return;}
        const data = response.data;
        if (data && (data.size > 0 || (data.byteLength && data.byteLength > 0))) {
          const blob = data instanceof Blob ? data : new Blob([data], { type: "image/png" });
          const newBlobUrl = URL.createObjectURL(blob);
          // Ownership transfers to the cache, which is why nothing revokes it
          // on unmount any more. See the module docstring.
          cacheSet(url, newBlobUrl);
          activeBlobUrlRef.current = null;
          setObjectUrl(newBlobUrl);
        } else {
          setObjectUrl(null);
        }
      })
      .catch(() => {
        if (active) {
          setObjectUrl(null);
        }
      });

    return () => {
      active = false;
    };
  }, [url]);

  // No unmount revocation. Object URLs live in `blobCache` and are revoked
  // only on LRU eviction or `clearAuthenticatedImageCache()`. Revoking here
  // would hand the next component to mount a dead URL.


  return objectUrl;
}

export default useAuthenticatedImage;
