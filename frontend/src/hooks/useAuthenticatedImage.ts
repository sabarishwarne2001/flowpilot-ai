/**
 * Fetch an authenticated image through the API client and expose it as an
 * object URL.
 *
 * ARCH-07 Step 7.
 */
import { useEffect, useRef, useState } from "react";
import apiClient from "@/services/api/client";

export function useAuthenticatedImage(url: string | null): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const activeBlobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!url) {
      if (activeBlobUrlRef.current) {
        URL.revokeObjectURL(activeBlobUrlRef.current);
        activeBlobUrlRef.current = null;
      }
      setObjectUrl(null);
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
        if (!active) return;
        const data = response.data;
        if (data && (data.size > 0 || (data.byteLength && data.byteLength > 0))) {
          const blob = data instanceof Blob ? data : new Blob([data], { type: "image/png" });
          const newBlobUrl = URL.createObjectURL(blob);
          if (activeBlobUrlRef.current) {
            URL.revokeObjectURL(activeBlobUrlRef.current);
          }
          activeBlobUrlRef.current = newBlobUrl;
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

  useEffect(() => {
    return () => {
      if (activeBlobUrlRef.current) {
        URL.revokeObjectURL(activeBlobUrlRef.current);
      }
    };
  }, []);

  return objectUrl;
}

export default useAuthenticatedImage;
