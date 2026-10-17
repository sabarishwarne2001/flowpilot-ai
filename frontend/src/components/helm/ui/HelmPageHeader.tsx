import React from "react";

/**
 * A Helm page's header (docs/helm/VISUAL.md "Typography"): an eyebrow (usually the book), the page's
 * one h1 in the large title style, a line of description and the page's actions on the right. It
 * rises in with the page.
 */
export const HelmPageHeader: React.FC<{
  readonly title: string;
  readonly eyebrow?: React.ReactNode;
  readonly description?: React.ReactNode;
  readonly actions?: React.ReactNode;
  readonly children?: React.ReactNode;
}> = ({ title, eyebrow, description, actions, children }) => (
  <header className="helm-enter flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
    <div className="min-w-0 max-w-3xl">
      {eyebrow ? <p className="helm-label">{eyebrow}</p> : null}
      <h1 className="helm-title mt-1">{title}</h1>
      {description ? <p className="helm-muted mt-2 text-sm leading-relaxed">{description}</p> : null}
      {children}
    </div>
    {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
  </header>
);

export default HelmPageHeader;
