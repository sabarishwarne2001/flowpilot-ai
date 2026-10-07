import React from "react";

import { Brand } from "@/components/branding/Brand";
import { usePublicBrandingManifest } from "@/hooks/usePublicBrandingManifest";
import { DocumentShowcase } from "./DocumentShowcase";

/**
 * Chrome for every page before sign-in: the form on an elevated card on the
 * left, the product on the right (desktop only).
 *
 * On a verified tenant custom domain with branding enabled, the FlowPilot
 * marketing pane is replaced with the tenant's name and, when set, their
 * support address. A white-labelled login that still advertises FlowPilot
 * beside the tenant's logo tells the tenant's employees which vendor they are
 * typing their password into — which is the thing white-label is sold to hide.
 *
 * Theme colours from the manifest are NOT applied here. The authenticated
 * application's theming path is where colour tokens belong, and applying them
 * pre-auth in a second place is a second implementation to keep in step.
 */
export const AuthShell: React.FC<{ readonly children: React.ReactNode }> = ({ children }) => {
  const manifest = usePublicBrandingManifest({ enabled: true });
  const custom = manifest?.has_custom_branding ? manifest : null;
  const brandLabel = custom?.brand_name ?? "FlowPilot AI";
  const year = new Date().getFullYear();

  const footer = (
    <div className="flex items-center justify-between text-xs text-zinc-500">
      <span>
        © {year} {brandLabel}
      </span>
      <span>All rights reserved.</span>
    </div>
  );

  return (
    <main className="grid min-h-dvh grid-cols-1 bg-background text-foreground lg:grid-cols-12">
      {/* The form */}
      <section className="relative flex min-h-dvh flex-col overflow-hidden px-5 py-6 sm:px-10 lg:col-span-5 xl:px-14">
        <div className="pointer-events-none absolute inset-0 fp-ambient opacity-80" aria-hidden="true" />
        <div className="pointer-events-none absolute inset-0 fp-grid opacity-60" aria-hidden="true" />

        <header aria-label="Application Brand" className="relative z-10 flex items-center justify-between">
          <Brand variant="login" />
        </header>

        <div className="relative z-10 flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-[420px] animate-slide-up">
            <div className="rounded-2xl border border-border/80 bg-card/85 p-6 shadow-elevation-3 backdrop-blur-xl dark:border-white/10 dark:bg-zinc-900/80 sm:p-8">
              {children}
            </div>
          </div>
        </div>

        <p className="relative z-10 text-center text-xs text-muted-foreground lg:hidden">
          © {year} {brandLabel}
        </p>
      </section>

      {/* The product (desktop) */}
      <aside className="dark relative hidden overflow-hidden border-l border-white/[0.06] bg-[#070709] text-foreground lg:col-span-7 lg:block">
        <div
          className="pointer-events-none absolute inset-0"
          aria-hidden="true"
          style={{
            backgroundImage:
              "radial-gradient(52rem 32rem at 85% -5%, rgba(59,130,246,0.22), transparent 62%), radial-gradient(40rem 28rem at -5% 105%, rgba(139,92,246,0.18), transparent 60%)",
          }}
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
