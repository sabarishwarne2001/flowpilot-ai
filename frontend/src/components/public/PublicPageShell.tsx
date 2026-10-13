import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { Brand } from "@/components/branding/Brand";
import { BUTTON_GHOST, BUTTON_PRIMARY } from "@/components/ui/primitives";
import { ROUTES } from "@/constants/routes";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * Campaign session 4 — chrome for the pages anyone may read: pricing, terms, privacy.
 *
 * Reachable signed out and signed in (routed outside PublicRoute and PrivateRoute), so the
 * header offers "Sign in" and "Start free trial" to a visitor and "Open the app" to someone
 * with a session. The page itself owns the single `h1`; this shell has none.
 */

export const PUBLIC_FOOTER_LINKS: ReadonlyArray<{ readonly to: string; readonly label: string }> = [
  { to: ROUTES.PRICING, label: "Pricing" },
  { to: ROUTES.TERMS, label: "Terms of service" },
  { to: ROUTES.PRIVACY, label: "Privacy policy" },
];

const NAV_LINK =
  "rounded-md px-2 py-1 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

interface PublicPageShellProps {
  /** The browser tab's title, before the product name. */
  readonly title: string;
  readonly children: React.ReactNode;
}

export const PublicPageShell: React.FC<PublicPageShellProps> = ({ title, children }) => {
  useDocumentTitle(title);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const year = new Date().getFullYear();

  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:shadow-elevation-2"
      >
        Skip to content
      </a>

      <header className="border-b border-border/70 bg-card/60 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <Link
            to={ROUTES.PRICING}
            className="flex min-w-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Brand variant="header" />
          </Link>
          <nav aria-label="Account" className="flex shrink-0 items-center gap-1 sm:gap-2">
            <Link to={ROUTES.PRICING} className={`${NAV_LINK} hidden sm:inline-flex`}>
              Pricing
            </Link>
            {isAuthenticated ? (
              <Link to={ROUTES.DASHBOARD} className={BUTTON_PRIMARY}>
                Open the app
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            ) : (
              <>
                <Link to={ROUTES.LOGIN} className={BUTTON_GHOST}>
                  Sign in
                </Link>
                <Link to={ROUTES.REGISTER} className={`${BUTTON_PRIMARY} hidden sm:inline-flex`}>
                  Start free trial
                </Link>
              </>
            )}
          </nav>
        </div>
      </header>

      <main id="main" className="flex-1">
        {children}
      </main>

      <footer className="border-t border-border/70">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
          <span>© {year} FlowPilot AI</span>
          <nav aria-label="Pricing and legal" className="flex flex-wrap items-center gap-x-1 gap-y-1">
            {PUBLIC_FOOTER_LINKS.map((link) => (
              <Link key={link.to} to={link.to} className={NAV_LINK}>
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
};

export default PublicPageShell;
