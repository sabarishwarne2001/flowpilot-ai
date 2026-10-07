import React from "react";
import { Outlet } from "react-router-dom";

import { AuthShell } from "@/components/auth/AuthShell";
import { usePublicBrandingManifest } from "@/hooks/usePublicBrandingManifest";
import { resolveApiAssetUrl } from "@/services/api/client";
import type { BrandingManifest } from "@/types/branding";

/**
 * Applies a tenant's name and favicon to the document while a pre-auth page
 * is mounted, and restores what was there on unmount.
 *
 * ARCH-30 Tranche 1 (T4-F5). The favicon endpoint (`/api/v1/branding/favicon`)
 * shipped in ARCH-25 with no consumer. A browser tab reading "FlowPilot AI"
 * with FlowPilot's icon on `ai.acme.com/login` undoes the white-label at the
 * one place a user looks before typing a password.
 *
 * Restoration matters because this layout unmounts into the authenticated
 * application, which manages its own title. An existing icon link is edited in
 * place rather than replaced, and a link this hook created is removed.
 */
function useDocumentBranding(brand: BrandingManifest | null): void {
  React.useEffect(() => {
    if (!brand) {
      return;
    }

    const previousTitle = document.title;
    if (brand.brand_name) {
      document.title = `Sign in · ${brand.brand_name}`;
    }

    const faviconHref = resolveApiAssetUrl(brand.favicon_url);
    let link: HTMLLinkElement | null = null;
    let previousHref: string | null = null;
    let created = false;

    if (faviconHref) {
      link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
      if (!link) {
        link = document.createElement("link");
        link.rel = "icon";
        document.head.appendChild(link);
        created = true;
      }
      previousHref = link.getAttribute("href");
      link.href = faviconHref;
    }

    return () => {
      document.title = previousTitle;
      if (!link) {
        return;
      }
      if (created) {
        link.remove();
      } else if (previousHref !== null) {
        link.setAttribute("href", previousHref);
      }
    };
  }, [brand]);
}

/**
 * Chrome for unauthenticated pages under the router: sign-in, registration,
 * SSO completion. The split-screen itself is AuthShell (shared with the
 * password and verification pages, which are routed on their own).
 */
export const AuthLayout = () => {
  const manifest = usePublicBrandingManifest({ enabled: true });
  const custom = manifest?.has_custom_branding ? manifest : null;

  useDocumentBranding(custom);

  return (
    <AuthShell>
      <Outlet />
    </AuthShell>
  );
};

export default AuthLayout;
