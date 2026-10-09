import React from "react";
import type { LucideIcon } from "lucide-react";

/**
 * Phase 3 — one header for every console page.
 *
 * Before this, the organization, admin, billing and identity consoles each wrote their own
 * `<h1>`: five sizes (text-base to text-2xl), icons on some, the organization name as the only
 * description on others, actions floating wherever there was room. A person moving from Members
 * to Billing to Webhooks saw three different products. Every console now opens the same way: an
 * icon tile, an eyebrow naming where the page lives, the title, one sentence on what it is for,
 * and the page's primary actions on the right (they wrap under the title on a phone).
 */
export interface PageHeaderProps {
  readonly title: React.ReactNode;
  readonly description?: React.ReactNode;
  readonly icon?: LucideIcon;
  /** Where the page lives ("Organization", "Platform"), or the organization's name. */
  readonly eyebrow?: React.ReactNode;
  /** A status pill or plan badge shown beside the title. */
  readonly badge?: React.ReactNode;
  readonly actions?: React.ReactNode;
  readonly className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  description,
  icon: Icon,
  eyebrow,
  badge,
  actions,
  className = "",
}) => (
  <header
    className={`flex flex-col gap-4 border-b border-border/60 pb-5 sm:flex-row sm:items-start sm:justify-between ${className}`}
  >
    <div className="flex min-w-0 items-start gap-3.5">
      {Icon ? (
        <span
          aria-hidden="true"
          className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border/70 bg-gradient-to-b from-card to-muted/70 text-foreground/80 shadow-elevation-1"
        >
          <Icon className="h-[18px] w-[18px]" />
        </span>
      ) : null}
      <div className="min-w-0">
        {eyebrow ? (
          <p className="truncate text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {eyebrow}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-[22px]">{title}</h1>
          {badge}
        </div>
        {description ? (
          <div className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</div>
        ) : null}
      </div>
    </div>
    {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
  </header>
);

export default PageHeader;
