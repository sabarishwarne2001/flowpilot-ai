import React from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";

import { useTenant } from "@/hooks/useTenant";
import { organizationBillingPath } from "@/routes/tenantPaths";

interface ViewPlansActionProps {
  /** Given on organization pages; read from the workspace context otherwise. */
  readonly organizationSlug?: string | undefined;
  readonly organizationRole?: string | undefined;
  readonly className?: string | undefined;
}

/**
 * F-059. The upgrade path on a lock card.
 *
 * A locked item clicked in the sidebar opens a dialog with "View plans"; the
 * same page opened by URL (a bookmark, a shared link) showed only a lock card
 * with no way forward. Every lock card now ends with this: a "View plans" link
 * to Billing for the roles that can change the plan (OWNER, BILLING), and the
 * name of who to ask for everyone else.
 */
export const ViewPlansAction: React.FC<ViewPlansActionProps> = ({
  organizationSlug,
  organizationRole,
  className = "",
}) => {
  const { state } = useTenant();
  const slug =
    organizationSlug ?? (state.status === "ready" ? state.organization.organization_slug : undefined);
  const role = String(
    organizationRole ?? (state.status === "ready" ? state.organizationRole : ""),
  ).toUpperCase();

  if (!slug) {
    return null;
  }
  if (role === "OWNER" || role === "BILLING") {
    return (
      <Link
        to={organizationBillingPath(slug)}
        data-testid="view-plans"
        className={`mt-3 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 ${className}`}
      >
        View plans
        <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
      </Link>
    );
  }
  return (
    <p className={`mt-2 text-xs text-muted-foreground ${className}`}>
      Ask an organization owner to upgrade the plan.
    </p>
  );
};

export default ViewPlansAction;
