/**
 * Fetch an authenticated image through the API client and expose it as an
 * object URL.
 *
 * ARCH-07 Step 7.
 */
import { useEffect, useState } from "react";
import { apiClient } from "@/services/api/client";

export function useAuthenticatedImage(url: string | null): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setObjectUrl(null);
      return;
    }
    let cancelled = false;
    let created: string | null = null;

    apiClient
      .get(url, { responseType: "blob" })
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
