import React, { useState } from "react";
import { Zap } from "lucide-react";

import { useOptionalTenant } from "@/routes/TenantContext";
import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";
import { usePublicBrandingManifest } from "@/hooks/usePublicBrandingManifest";
import { resolveApiAssetUrl } from "@/services/api/client";

export type BrandVariant =
  | "sidebar"
  | "sidebar-compact"
  | "header"
  | "login"
  | "loading";

interface BrandProps {
  variant?: BrandVariant;
  className?: string;
}

const BRAND_VARIANTS = {
  sidebar: {
    logo: "h-8 w-8",
    compact: false,
    subtitle: true,
  },
  "sidebar-compact": {
    logo: "h-8 w-8",
    compact: true,
    subtitle: false,
  },
  header: {
    logo: "h-9 w-9",
    compact: false,
    subtitle: false,
  },
  login: {
    logo: "h-12 w-12",
    compact: false,
    subtitle: true,
  },
  loading: {
    logo: "h-8 w-8",
    compact: false,
    subtitle: true,
  },
} as const;

function BrandSkeleton({ variant }: { variant: BrandVariant }) {
  const config = BRAND_VARIANTS[variant];
  const showText = variant !== "sidebar-compact";

  return (
    <div
      className={`flex items-center ${
        showText ? "gap-3" : "justify-center"
      } animate-pulse`}
    >
      <div className={`${config.logo} rounded-lg bg-muted`} />

      {showText && (
        <div className="space-y-2">
          <div className="h-4 w-36 rounded bg-muted" />
          <div className="h-3 w-24 rounded bg-muted" />
        </div>
      )}
    </div>
  );
}

/**
 * The product mark, in whichever identity applies to this surface.
 *
 * THREE SOURCES, IN PRIORITY ORDER
 * ================================
 *
 * 1. A resolved tenant (authenticated, inside a workspace). Logo via
 *    `useAuthenticatedImage`, which attaches the session — unchanged.
 * 2. ARCH-30 Tranche 1 (T4-F5): no tenant, but the page was served on a
 *    verified tenant custom domain. The host-resolved public manifest supplies
 *    the brand name and an UNAUTHENTICATED logo URL. This is what makes
 *    `ai.acme.com/login` say Acme before anyone has signed in.
 * 3. Neither: FlowPilot defaults.
 *
 * ARCH-29 finding Issue 5 established why (2) could not be done in this
 * component alone: pre-auth there is no tenant to read and no session to fetch
 * with. ARCH-25 had already built the server half — a public endpoint keyed by
 * Host — and nothing consumed it.
 *
 * The manifest is only requested when there is no tenant. Inside a workspace
 * the tenant's own logo is authoritative and a second, host-keyed opinion
 * about branding would only be a chance to disagree.
 *
 * `has_custom_branding` gates (2) rather than a non-null `brand_name`: the
 * platform origin and a tenant with branding disabled both return the
 * default manifest, and the flag is the server's statement that the values
 * are a tenant's rather than defaults to display.
 */
export const Brand: React.FC<BrandProps> = ({
  variant = "sidebar",
  className = "",
}) => {
  const tenant = useOptionalTenant();
  const [imgError, setImgError] = useState(false);

  const manifest = usePublicBrandingManifest({ enabled: tenant === null });
  const publicBrand =
    tenant === null && manifest?.has_custom_branding ? manifest : null;

  const workspaceName =
    tenant?.workspace.workspace_name ?? publicBrand?.brand_name ?? "FlowPilot AI";
  const companyName =
    tenant?.organization.organization_name ??
    (publicBrand ? "Secure sign-in" : "AI Document Intelligence");

  const logoPath = tenant?.workspace.company_logo_url ?? null;
  const authenticatedLogo = useAuthenticatedImage(logoPath);

  // `resolveApiAssetUrl`, never the raw manifest string: the manifest is
  // unauthenticated input, and the helper refuses any URL off the API origin.
  const publicLogo = resolveApiAssetUrl(publicBrand?.logo_url);
  const logoSrc = authenticatedLogo ?? publicLogo;

  const initials = (workspaceName || companyName || "FP")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word: string) => word.charAt(0).toUpperCase())
    .join("");

  const config = BRAND_VARIANTS[variant];
  const compact = config.compact;
  const showSubtitle = config.subtitle;
  const isAuthPage = variant === "login" || !tenant;

  return (
    <div
      className={`
        flex
        items-center
        transition-all
        duration-300
        ease-in-out
        ${compact ? "justify-center" : "gap-3"}
        overflow-hidden
        ${className}
      `}
    >
      <div
        className={`
          ${config.logo}
          flex
          shrink-0
          items-center
          justify-center
          overflow-hidden
          rounded-lg
          ${logoSrc && !imgError ? "border border-border bg-background" : "bg-primary text-primary-foreground"}
        `}
      >
        {logoSrc && !imgError ? (
          <img
            src={logoSrc}
            alt={publicBrand?.brand_name ?? companyName}
            className="h-full w-full object-cover"
            onError={() => setImgError(true)}
          />
        ) : isAuthPage && !publicBrand ? (
          <div className="flex h-full w-full items-center justify-center bg-primary">
            <Zap className="h-6 w-6 text-primary-foreground fill-primary-foreground" />
          </div>
        ) : (
          <span className="font-black text-xs sm:text-sm text-primary-foreground">
            {initials}
          </span>
        )}
      </div>

      {!compact && (
        <div className="flex-1 min-w-0 overflow-hidden">
          <div
            className="
              text-base
              font-bold
              leading-tight
              whitespace-nowrap
              overflow-hidden
              text-ellipsis
            "
          >
            {workspaceName}
          </div>

          {showSubtitle && (
            <div className="truncate text-xs text-muted-foreground">
              {companyName}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export { BrandSkeleton };

export default Brand;
