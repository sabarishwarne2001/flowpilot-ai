/**
 * ARCH-37 — the shell every builder step shares: a numbered card with its
 * own issue list, so a server refusal lands where the user is looking.
 */

import React from "react";
import { AlertTriangle } from "lucide-react";

import type { CardIssue } from "./flowModel";

interface StepCardProps {
  readonly step: number;
  readonly title: string;
  readonly subtitle: string;
  readonly icon: React.ReactNode;
  readonly issues: readonly CardIssue[];
  readonly children: React.ReactNode;
  readonly aside?: React.ReactNode;
  readonly id: string;
}

export const StepCard: React.FC<StepCardProps> = ({ step, title, subtitle, icon, issues, children, aside, id }) => {
  const cardLevel = issues.filter((issue) => issue.index === null);
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className={`rounded-xl border bg-card shadow-sm ${issues.length ? "border-destructive/60" : "border-border"}`}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border/50 px-5 py-4">
        <div className="flex items-start gap-3 min-w-0">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-black text-primary">
            {step}
          </span>
          <div className="min-w-0">
            <h3 id={`${id}-title`} className="flex items-center gap-2 text-sm font-extrabold uppercase tracking-wider">
              <span aria-hidden="true" className="text-primary">{icon}</span>
              {title}
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
          </div>
        </div>
        {aside}
      </header>
      {cardLevel.length > 0 && (
        <ul className="mx-5 mt-4 space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2" role="alert">
          {cardLevel.map((issue, index) => (
            <li key={index} className="flex items-start gap-2 text-xs font-semibold text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {issue.message}
            </li>
          ))}
        </ul>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
};

export const IssueText: React.FC<{ readonly issues: readonly CardIssue[] }> = ({ issues }) =>
  issues.length ? (
    <p className="mt-1 text-xs font-semibold text-destructive" role="alert">
      {issues.map((issue) => issue.message).join(" ")}
    </p>
  ) : null;

export const inputClass = (invalid: boolean): string =>
  `w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    invalid
      ? "border-destructive focus:border-destructive focus:ring-destructive/20"
      : "border-border focus:border-primary focus:ring-primary/20"
  }`;
