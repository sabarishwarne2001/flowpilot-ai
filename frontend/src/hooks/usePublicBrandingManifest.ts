import { useQuery } from "@tanstack/react-query";

import { getPublicBrandingManifest } from "@/services/api/branding";
import { brandingKeys } from "@/services/api/queryKeys";
import type { BrandingManifest } from "@/types/branding";

interface UsePublicBrandingManifestOptions {
  /**
   * False where a resolved tenant already supplies branding. The manifest is
   * for surfaces with no session and no tenant context: the login page, the
   * SSO completion page, the workspace picker, the loading screen.
   */
  readonly enabled: boolean;
}

/**
 * ARCH-30 Tranche 1 (T4-F5) — the host-resolved theme, for pre-auth surfaces.
 *
 * Returns null while loading, on error, and when disabled. Every consumer
 * treats null as "render FlowPilot defaults", so a failed request degrades to
 * the platform look rather than to an empty header. That is the right
 * direction: on the platform origin the manifest IS the default, and on a
 * tenant host a missing logo is cosmetic, whereas a login page that blocks on
 * branding is an outage.
 *
 * `retry: false` because the only failures worth retrying are transient, and
 * a login page that spins through three retries before showing the form is
 * worse than one that shows the form without a logo. `staleTime` matches the
 * server's `BRANDING_MANIFEST_CACHE_SECONDS` default of 60.
 *
 * The query key is not organization-scoped, deliberately: the request is not
 * addressed by organization. Brand and AuthLayout both call this hook and
 * TanStack Query deduplicates them into one request.
 */
export function usePublicBrandingManifest({
  enabled,
}: UsePublicBrandingManifestOptions): BrandingManifest | null {
  const query = useQuery({
    queryKey: brandingKeys.manifest,
    queryFn: getPublicBrandingManifest,
    enabled,
    staleTime: 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });

  return query.data ?? null;
}

export default usePublicBrandingManifest;
