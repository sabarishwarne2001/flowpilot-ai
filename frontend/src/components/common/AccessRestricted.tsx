import React from "react";
import { Link } from "react-router-dom";
import { ShieldAlert } from "lucide-react";

interface AccessRestrictedProps {
  /** Who may use the page, in words: "organization owners and administrators". */
  readonly allowedFor: string;
  /** Who to ask, in words: "an organization owner or administrator". */
  readonly askWho: string;
  /** Where the "go back" link leads, and its label. */
  readonly backTo?: { readonly path: string; readonly label: string } | undefined;
}

/**
 * F-008 / F-053 / F-054. The screen a role sees on a page it may not use.
 *
 * Before, such a page fired its requests anyway, the server refused them with
 * 403 and the page said "… couldn't be loaded. Try again", which reads as an
 * outage. This is rendered INSTEAD of the page, so no request is made. It is
 * an affordance, not the boundary: the server still refuses every call.
 */
export const AccessRestricted: React.FC<AccessRestrictedProps> = ({ allowedFor, askWho, backTo }) => (
  <section
    role="status"
    aria-labelledby="access-restricted-title"
    data-testid="access-restricted"
    className="mx-auto mt-8 flex max-w-xl flex-col items-center gap-3 rounded-2xl border border-dashed border-border-strong/70 bg-card/60 px-6 py-12 text-center animate-fade-in"
  >
    <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-muted to-muted/30 shadow-elevation-1">
      <ShieldAlert className="h-5 w-5 text-muted-foreground" aria-hidden />
    </span>
    <h1 id="access-restricted-title" className="text-base font-semibold text-foreground">
      Access restricted
    </h1>
    <p className="text-sm text-muted-foreground">
      This page is available to {allowedFor}. You don&apos;t have access with your current role.
    </p>
    <p className="text-sm text-muted-foreground">
      If you need it, contact your administrator: ask {askWho} to change your role.
    </p>
    {backTo ? (
      <Link
        to={backTo.path}
        className="fp-btn fp-btn-secondary mt-2"
      >
        {backTo.label}
      </Link>
    ) : null}
  </section>
);

export default AccessRestricted;
