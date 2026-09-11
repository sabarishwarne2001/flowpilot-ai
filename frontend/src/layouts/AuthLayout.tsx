import React from "react";
import { Outlet } from "react-router-dom";

import { Brand } from "@/components/branding/Brand";
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
 * Chrome for unauthenticated pages: sign-in, registration, SSO completion.
 *
 * On a verified tenant custom domain with branding enabled, the FlowPilot
 * marketing copy and copyright are replaced with the tenant's name and, when
 * set, their support address. A white-labelled login that still advertises
 * "Document intelligence and workflow automation at scale" beside the
 * tenant's logo tells the tenant's employees which vendor they are typing
 * their password into — which is the thing white-label is sold to hide.
 *
 * Theme colours from the manifest are NOT applied here. The authenticated
 * application's theming path is where colour tokens belong, and applying them
 * pre-auth in a second place is a second implementation to keep in step.
 */
export const AuthLayout = () => {
  const manifest = usePublicBrandingManifest({ enabled: true });
  const custom = manifest?.has_custom_branding ? manifest : null;
  const brandLabel = custom?.brand_name ?? "FlowPilot AI";

  useDocumentBranding(custom);

  return (
    <main className="min-h-dvh grid grid-cols-1 lg:grid-cols-12 bg-background text-foreground transition-colors duration-200">
      {/* Authentication Form Area */}
      <section className="relative flex flex-col justify-center px-4 py-8 sm:px-8 sm:py-12 xl:px-16 lg:col-span-5 min-h-dvh">
        <header
          aria-label="Application Brand"
          className="mb-8 flex justify-center sm:justify-start"
        >
          <Brand variant="login" />
        </header>

        <div className="mx-auto w-full max-w-md">
          <Outlet />
        </div>
      </section>

      {/* Hero Section (Hidden on mobile, visible on desktop) */}
      <aside className="relative hidden overflow-hidden border-l border-border bg-muted/50 p-12 dark:bg-muted/10 lg:col-span-7 lg:flex lg:flex-col lg:justify-between xl:p-16">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-tr from-primary/10 via-transparent to-transparent" />
        <div className="pointer-events-none absolute -right-12 -top-12 h-96 w-96 rounded-full bg-primary/5 blur-3xl" />

        {custom ? (
          <div className="z-10 mt-auto max-w-lg">
            <h2 className="mb-4 text-3xl font-extrabold tracking-tight xl:text-4xl">
              {brandLabel}
            </h2>

            <p className="text-sm font-medium leading-relaxed text-muted-foreground xl:text-base">
              Sign in to continue to your workspace.
            </p>

            {custom.support_email && (
              <p className="mt-4 text-sm text-muted-foreground">
                Need help? Contact{" "}
                <a
                  href={`mailto:${custom.support_email}`}
                  className="underline underline-offset-4 hover:text-primary"
                >
                  {custom.support_email}
                </a>
              </p>
            )}
          </div>
        ) : (
          <div className="z-10 mt-auto max-w-lg">
            <h2 className="mb-4 text-3xl font-extrabold tracking-tight xl:text-4xl">
              Document intelligence and workflow automation at scale.
            </h2>

            <p className="text-sm font-medium leading-relaxed text-muted-foreground xl:text-base">
              Eliminate manual workflows with AI-powered document processing,
              semantic search, automation, and intelligent assistants built for
              modern businesses.
            </p>
          </div>
        )}

        <footer className="z-10 mt-8 flex items-center justify-between border-t border-border/40 pt-8 text-xs text-muted-foreground/60">
          <span>
            © {new Date().getFullYear()} {brandLabel}
          </span>
          <span>All rights reserved.</span>
        </footer>
      </aside>
    </main>
  );
};

export default AuthLayout;
