import React from "react";
import { Link } from "react-router-dom";
import { FileWarning } from "lucide-react";

import { PublicPageShell } from "@/components/public/PublicPageShell";
import { HINT } from "@/components/ui/primitives";
import { ROUTES } from "@/constants/routes";

/**
 * Campaign session 4 — the terms and privacy pages, as placeholders for the owner's legal review.
 *
 * Owner decision N-029: no legal or compliance claim the product cannot back. These pages
 * therefore state no facts at all: every section is a heading, a neutral note of what it will
 * cover, and "[To be completed by the owner's legal counsel]". No certification, sub-processor,
 * retention period, jurisdiction or data-residency statement belongs here until counsel writes it.
 */

export const TO_BE_COMPLETED = "[To be completed by the owner's legal counsel]";

export interface LegalSection {
  readonly id: string;
  readonly heading: string;
  /** What the section will cover, in neutral words. Never a statement of fact. */
  readonly covers: string;
}

interface LegalPlaceholderProps {
  readonly title: string;
  readonly documentName: string;
  readonly sections: readonly LegalSection[];
}

export const LegalPlaceholder: React.FC<LegalPlaceholderProps> = ({ title, documentName, sections }) => (
  <PublicPageShell title={title}>
    <article className="mx-auto w-full max-w-3xl space-y-8 px-4 py-10 sm:px-6 lg:px-8" aria-labelledby="legal-title">
      <header className="space-y-4">
        <h1 id="legal-title" className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          {title}
        </h1>
        <div
          role="note"
          aria-label="Placeholder notice"
          data-testid="legal-placeholder-banner"
          className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-200"
        >
          <FileWarning className="mt-0.5 h-5 w-5 flex-shrink-0" aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-semibold">Placeholder — pending the owner&apos;s legal review</p>
            <p>
              This page is not the final {documentName} and makes no statements of fact. Its sections show what the
              final text will cover; the owner&apos;s legal counsel will write it.
            </p>
          </div>
        </div>
        <p className={HINT}>Effective date: {TO_BE_COMPLETED}</p>
      </header>

      {sections.map((section) => (
        <section key={section.id} id={section.id} aria-labelledby={`${section.id}-heading`} className="space-y-2">
          <h2 id={`${section.id}-heading`} className="text-lg font-semibold tracking-tight text-foreground">
            {section.heading}
          </h2>
          <p className="text-sm leading-relaxed text-muted-foreground">Will cover: {section.covers}</p>
          <p className="text-sm italic leading-relaxed text-foreground">{TO_BE_COMPLETED}</p>
        </section>
      ))}

      <p className={`${HINT} border-t border-border/70 pt-6`}>
        Prices and plan allowances are on the{" "}
        <Link
          to={ROUTES.PRICING}
          className="font-medium text-primary underline-offset-4 hover:underline dark:text-[hsl(var(--primary-on-dark))]"
        >
          pricing page
        </Link>
        .
      </p>
    </article>
  </PublicPageShell>
);

export default LegalPlaceholder;
