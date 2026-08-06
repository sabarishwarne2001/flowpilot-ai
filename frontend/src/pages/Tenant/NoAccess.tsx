/**
 * Tenant unavailability tombstone for FlowPilot AI.
 *
 * Shown when a tenant exists and the actor belongs to it, but it cannot
 * currently serve requests — a suspended organization, an archived workspace.
 *
 * Deliberately distinct from a permission error. The backend gives these their
 * own error code (TENANT_SUSPENDED, 403) rather than folding them into
 * PERMISSION_DENIED, precisely so the client can explain what happened instead
 * of showing a generic denial. "This workspace was archived" and "you don't
 * have permission" send a user to two different places: one to an
 * administrator, the other to support.
 */

import React from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Archive, LifeBuoy } from "lucide-react";

import { ROUTES } from "@/constants/routes";

type NoAccessReason = "organization_suspended" | "workspace_archived" | "unknown";

interface NoAccessState {
  reason?: NoAccessReason;
  detail?: string;
}

const REASON_COPY: Record<NoAccessReason, { title: string; body: string }> = {
  organization_suspended: {
    title: "This organization is unavailable",
    body: "It has been suspended or archived. An organization owner can restore access, and no data has been deleted.",
  },
  workspace_archived: {
    title: "This workspace is unavailable",
    body: "It has been archived or suspended. An organization admin can restore it, and its documents are retained.",
  },
  unknown: {
    title: "This tenant is unavailable",
    body: "It cannot be accessed right now. No data has been deleted — an organization owner or admin can restore access.",
  },
};

export const NoAccess: React.FC = () => {
  const location = useLocation();
  const [searchParams] = useSearchParams();

  const state = location.state as NoAccessState | null;

  const reason: NoAccessReason =
    state?.reason ??
    ((searchParams.get("reason") as NoAccessReason | null) ?? "unknown");

  const copy = REASON_COPY[reason] ?? REASON_COPY.unknown;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-md space-y-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-muted">
          <Archive className="h-5 w-5 text-muted-foreground" />
        </div>

        <div className="space-y-2">
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            {copy.title}
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {copy.body}
          </p>
          {state?.detail && (
            <p className="text-xs text-muted-foreground">{state.detail}</p>
          )}
        </div>

        <div className="space-y-2">
          <Link
            to={ROUTES.WORKSPACES}
            className="block w-full rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
          >
            Choose another workspace
          </Link>

          <p className="flex items-center justify-center gap-1.5 pt-1 text-xs text-muted-foreground">
            <LifeBuoy className="h-3.5 w-3.5" />
            Contact an organization owner if you believe this is a mistake.
          </p>
        </div>
      </div>
    </div>
  );
};

export default NoAccess;
