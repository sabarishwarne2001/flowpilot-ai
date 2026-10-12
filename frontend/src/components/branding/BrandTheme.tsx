import { useEffect } from "react";

import { usePublicBrandingManifest } from "@/hooks/usePublicBrandingManifest";
import { applyBrandColours } from "@/utils/brandTheme";

/**
 * Campaign session 3 (F-300, N-065): the brand colours of the organization whose verified domain
 * served this page, on every page of it, signed in or not. On the platform's own address the
 * manifest is the platform default and nothing changes. See `@/utils/brandTheme` for which
 * colour shows where.
 */
export const BrandTheme = (): null => {
  const manifest = usePublicBrandingManifest({ enabled: true });
  useEffect(() => {
    applyBrandColours(manifest);
  }, [manifest]);
  return null;
};

export default BrandTheme;
