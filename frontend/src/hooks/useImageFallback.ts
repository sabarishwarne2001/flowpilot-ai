import { useCallback, useState } from "react";

import { forgetBrokenImage } from "@/hooks/useAuthenticatedImage";

/**
 * F-143 — an image that fails to load shows its fallback, never a broken-image icon.
 *
 *     const { failed, onError } = useImageFallback(src);
 *     return src && !failed ? <img src={src} onError={onError} /> : <Initials />;
 *
 * The failure is remembered for the exact `src` that failed, so a new picture
 * (another object URL, another `?v=`) is tried again at once. Several surfaces
 * used to keep a plain `imgError` flag that was never reset: after one bad
 * load the header kept showing initials even once a good logo was uploaded,
 * and the profile picture's handler refetched on every error, which looped
 * for as long as the stored picture could not be decoded.
 */
export function useImageFallback(src: string | null | undefined): {
  readonly failed: boolean;
  readonly onError: () => void;
} {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const failed = Boolean(src) && failedSrc === src;

  const onError = useCallback(() => {
    if (!src) {return;}
    setFailedSrc(src);
    if (src.startsWith("blob:")) {
      forgetBrokenImage(src);
    }
  }, [src]);

  return { failed, onError };
}

export default useImageFallback;
