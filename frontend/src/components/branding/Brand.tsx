import React, { useState } from "react";

import { useOptionalTenant } from "@/routes/TenantContext";

/**
 * Brand mark for FlowPilot AI.
 */

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
    logo: "h-14 w-14",
    compact: false,
    subtitle: true,
  },

  loading: {
    logo: "h-8 w-8",
    compact: false,
    subtitle: true,
  },
} as const;

const API_ORIGIN = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"
).replace("/api/v1", "");

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

export const Brand: React.FC<BrandProps> = ({
  variant = "sidebar",
  className = "",
}) => {
  const tenant = useOptionalTenant();

  const [logoError, setLogoError] = useState(false);

  const workspaceName = tenant?.workspace.workspace_name ?? "FlowPilot AI";

  const companyName =
    tenant?.organization.organization_name ?? "AI Document Intelligence";

  const logoPath = tenant?.workspace.company_logo_url ?? null;

  const logo = logoPath ? `${API_ORIGIN}${logoPath}` : null;

  const initials = (workspaceName || companyName || "FP")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word: string) => word.charAt(0).toUpperCase())
    .join("");

  const config = BRAND_VARIANTS[variant];

  const compact = config.compact;

  const showSubtitle = config.subtitle;

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
          bg-primary
        `}
      >
        {logo && !logoError ? (
          <img
            src={logo}
            alt={companyName}
            className="h-full w-full object-cover"
            onError={() => setLogoError(true)}
          />
        ) : (
          <span className="font-black text-primary-foreground">{initials}</span>
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
