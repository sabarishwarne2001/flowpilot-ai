import React, { useEffect } from "react";
import { Link } from "react-router-dom";

import { Brand } from "@/components/branding/Brand";
import { PUBLIC_PAGE_LINKS } from "@/constants/routes";
import { usePublicBrandingManifest } from "@/hooks/usePublicBrandingManifest";
import { isHexColour } from "@/utils/brandTheme";
import { DocumentShowcase } from "./DocumentShowcase";
import { AUTH_MUTED_LINK } from "./authStyles";

/**
 * Chrome for every page before sign-in: the form on an elevated card on the
 * left, the product on the right (desktop only).
 *
 * On a verified tenant custom domain with branding enabled, the Servabo
 * marketing pane is replaced with the tenant's name and, when set, their
 * support address. A white-labelled login that still advertises Servabo
 * beside the tenant's logo tells the tenant's employees which vendor they are
 * typing their password into — which is the thing white-label is sold to hide.
 *
 * Campaign session 3 (F-300, N-065): the tenant's colours apply too. The
 * primary and accent colours reach every page on the domain through
 * BrandTheme; here the background and text colours paint the backdrop, the
 * side pane glows in the primary and accent colours, and the colour scheme
 * decides light or dark while a sign-in page is open (inside the app light or
 * dark stays each person's own choice). The form card keeps the palette's own
 * card colours, so a dark backdrop never leaves dark text on a white card.
 */
export const AuthShell: React.FC<{ readonly children: React.ReactNode }> = ({ children }) => {
  const manifest = usePublicBrandingManifest({ enabled: true });
  const custom = manifest?.has_custom_branding ? manifest : null;
  const brandLabel = custom?.brand_name ?? "Servabo";
  const year = new Date().getFullYear();
  const scheme = custom?.color_scheme ?? "SYSTEM";

  useEffect(() => {
    if (scheme === "SYSTEM") {
      return undefined;
    }
    const root = document.documentElement;
    const before = root.classList.contains("dark");
    root.classList.toggle("dark", scheme === "DARK");
    return () => {
      root.classList.toggle("dark", before);
    };
  }, [scheme]);

  const backdrop: React.CSSProperties = {
    ...(isHexColour(custom?.background_color) ? { backgroundColor: custom.background_color } : {}),
    ...(isHexColour(custom?.foreground_color) ? { color: custom.foreground_color } : {}),
  };
  // Eight-digit hex: the colour at 22% and 18% opacity, as the default glow.
  const glow = (hex: string | null | undefined, fallback: string, alpha: string): string =>
    isHexColour(hex) ? `${hex}${alpha}` : fallback;
  const sideGlow = `radial-gradient(52rem 32rem at 85% -5%, ${glow(custom?.primary_color, "rgba(59,130,246,0.22)", "38")}, transparent 62%), radial-gradient(40rem 28rem at -5% 105%, ${glow(custom?.accent_color, "rgba(139,92,246,0.18)", "2e")}, transparent 60%)`;

  const footer = (
    <div className="flex items-center justify-between text-xs text-zinc-500">
      <span>
        © {year} {brandLabel}
      </span>
      <span>All rights reserved.</span>
    </div>
  );

  return (
    <main className="grid min-h-dvh grid-cols-1 bg-background text-foreground lg:grid-cols-12" style={backdrop}>
      {/* The form */}
      <section className="relative flex min-h-dvh flex-col overflow-hidden px-5 py-6 sm:px-10 lg:col-span-5 xl:px-14">
        <div className="pointer-events-none absolute inset-0 fp-ambient opacity-80" aria-hidden="true" />
        <div className="pointer-events-none absolute inset-0 fp-grid opacity-60" aria-hidden="true" />

        <header aria-label="Application Brand" className="relative z-10 flex items-center justify-between">
          <Brand variant="login" />
        </header>

        <div className="relative z-10 flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-[420px] animate-slide-up">
            <div className="rounded-2xl border border-border/80 bg-card/85 p-6 text-card-foreground shadow-elevation-3 backdrop-blur-xl dark:border-white/10 dark:bg-zinc-900/80 sm:p-8">
              {children}
            </div>
          </div>
        </div>

        <footer className="relative z-10 flex flex-col items-center gap-2 text-center text-xs text-muted-foreground">
          {/* Campaign session 4: pricing, terms and privacy, on the platform's own address only.
              On a white-labelled domain Servabo's pages would name the vendor the tenant hides. */}
          {custom ? null : (
            <nav aria-label="Pricing and legal" className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
              {PUBLIC_PAGE_LINKS.map((link) => (
                <Link key={link.to} to={link.to} className={AUTH_MUTED_LINK}>
                  {link.label}
                </Link>
              ))}
            </nav>
          )}
          <p className="lg:hidden">
            © {year} {brandLabel}
          </p>
        </footer>
      </section>

      {/* The product (desktop) */}
      <aside className="dark relative hidden overflow-hidden border-l border-white/[0.06] bg-[#070709] text-foreground lg:col-span-7 lg:block">
        <div
          className="pointer-events-none absolute inset-0"
          aria-hidden="true"
          style={{ backgroundImage: sideGlow }}
        />
        <div className="pointer-events-none absolute inset-0 fp-grid opacity-70" aria-hidden="true" />

        {custom ? (
          <div className="relative z-10 flex h-full flex-col justify-end p-10 xl:p-14">
            <div className="max-w-lg">
              <h2 className="mb-4 text-3xl font-semibold tracking-tight text-white xl:text-4xl">{brandLabel}</h2>
              <p className="text-[15px] leading-relaxed text-zinc-400">Sign in to continue to your workspace.</p>
              {custom.support_email && (
                <p className="mt-4 text-sm text-zinc-400">
                  Need help? Contact{" "}
                  <a
                    href={`mailto:${custom.support_email}`}
                    className="text-zinc-200 underline underline-offset-4 hover:text-white"
                  >
                    {custom.support_email}
                  </a>
                </p>
              )}
            </div>
            <div className="mt-10 border-t border-white/[0.06] pt-5">{footer}</div>
          </div>
        ) : (
          <DocumentShowcase footer={footer} />
        )}
      </aside>
    </main>
  );
};

export default AuthShell;
