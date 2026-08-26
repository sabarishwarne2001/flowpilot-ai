/**
 * Fetch an authenticated image through the API client and expose it as an
 * object URL.
 *
 * ARCH-07 Step 7.
 */
import { useEffect, useState } from "react";
import apiClient from "@/services/api/client";

export function useAuthenticatedImage(url: string | null): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setObjectUrl(null);
      return;
    }

    // Direct bypass for local blob or data URLs (instant preview during file selection)
    if (url.startsWith("blob:") || url.startsWith("data:")) {
      setObjectUrl(url);
      return;
    }

    let cancelled = false;
    let created: string | null = null;

    // Normalize endpoint path to prevent double /api/v1 prefixing
    const normalizedUrl = url.replace(/^\/api\/v1/, "");

    apiClient
      .get(normalizedUrl, { responseType: "blob" })
      .then((response) => {
        if (cancelled) return;
        created = URL.createObjectURL(response.data);
        setObjectUrl(created);
      })
      .catch(() => {
        if (!cancelled) setObjectUrl(null);
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  return objectUrl;
}

export default useAuthenticatedImage;
