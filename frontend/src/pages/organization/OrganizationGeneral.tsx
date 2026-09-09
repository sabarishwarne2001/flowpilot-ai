/**
 * Organization General Settings — ARCH-01 / ARCH-05.
 *
 * WHY THIS PAGE DID NOT EXIST
 *
 * `src/pages/organization/` held eleven pages — members, billing, branding,
 * compliance, SLOs, identity, webhooks and so on — and no home for the
 * organization ITSELF. The consequences were both visible:
 *
 *   1. Organization name was edited from Settings/Workspace.tsx, under a
 *      field labelled `company_name`. An organization-level mutation living
 *      on a workspace-level screen, named after neither.
 *
 *   2. `archiveOrganization` was exported from organizationApi with zero
 *      callers anywhere in the repository. The endpoint worked; nothing in
 *      the product could reach it. An owner had no way to wind down an
 *      organization short of a support request.
 *
 * ROUTE_PATTERNS.organizationSettings ("settings") and organizationSettingsPath
 * both already existed in tenantPaths.ts. The slot was carved out and never
 * filled. This fills it.
 *
 * ARCHIVE, NOT DELETE — AND THE LABEL IS THE POINT
 *
 * Hard deletion of an organization is not implemented, and is not a gap. It is
 * blocked by three mechanisms this architecture put in place deliberately:
 *
 *   - `billing_accounts.organization_id` is ON DELETE RESTRICT, so DELETE
 *     raises a foreign key violation for any organization that has ever had a
 *     billing account.
 *   - `audit_logs` cascades from organizations, and `trg_audit_logs_immutable`
 *     is a BEFORE DELETE row trigger. Cascade deletes fire row triggers, so
 *     the cascade aborts the transaction.
 *   - `usage_events` and `usage_rollups` carry the same immutability triggers.
 *
 * Making DELETE work would mean dropping RESTRICT on billing and disabling
 * immutability on three ledgers — dismantling ARCH-07 audit immutability,
 * ARCH-14 metering integrity and ARCH-24 financial close so that a button can
 * exist. It would also contradict a decision already made correctly elsewhere:
 * ARCH-20 subject erasure works by overwrite, not delete, precisely so
 * referential integrity survives.
 *
 * So this says "Archive", never "Delete", in the heading, the button, the
 * confirmation and the toast. Archiving deactivates access and API keys and
 * preserves the financial and audit record. Calling that "Delete" would
 * promise an erasure that is not performed — under GDPR a claim you cannot
 * substantiate, and to an enterprise buyer the opposite of reassuring. What
 * they want to hear is that the records survive.
 *
 * TYPED CONFIRMATION, INLINE
 *
 * The shared ConfirmDialog takes a message and a button; it has no notion of
 * a typed confirmation. Rather than widen a component used across the product
 * for one caller, the confirmation is built here. Typing the slug is not
 * theatre: it is the one interaction that cannot be completed by muscle memory
 * on a dialog the actor has dismissed a hundred times before.
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle,
  Archive,
  Building2,
  Check,
  Loader2,
  Save,
} from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  archiveOrganization,
  updateOrganization,
} from "@/services/api/organization";
import { ApiError } from "@/services/api/errors";
import {
  canDeleteOrganization,
  canManageOrganizationSettings,
} from "@/permissions/organizationPermissions";
import type { OrganizationRole } from "@/types/tenancy";

/* ==========================================================================
 * Status pill
 * ========================================================================== */

const STATUS_STYLES: Record<string, string> = {
  ACTIVE:
    "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  SUSPENDED:
    "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  ARCHIVED: "border-border/60 bg-muted/40 text-muted-foreground",
};

const StatusPill: React.FC<{ status: string }> = ({ status }) => (
  <span
    className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
      STATUS_STYLES[status] ?? STATUS_STYLES.ARCHIVED
    }`}
  >
    {status.toLowerCase()}
  </span>
);

/* ==========================================================================
 * Page
 * ========================================================================== */

export const OrganizationGeneral: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const role = organizationRole as OrganizationRole;
  const canEdit = canManageOrganizationSettings(role);
  const canArchive = canDeleteOrganization(role);

  const slug = organization.organization_slug;
  const status = organization.organization_status;
  const isArchived = status !== "ACTIVE";

  const [name, setName] = useState(organization.organization_name);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typedSlug, setTypedSlug] = useState("");

  const nameChanged = useMemo(
    () => name.trim() !== organization.organization_name && name.trim() !== "",
    [name, organization.organization_name],
  );

  /* --- Profile ---------------------------------------------------------- */

  const { mutate: saveProfile, isPending: isSaving } = useMutation({
    mutationFn: () => updateOrganization(organizationId, { name: name.trim() }),
    onSuccess: (updated) => {
      toast.success("Organization updated.");
      // The name appears in the sidebar, the header and the workspace picker,
      // all of which read from the bootstrap context rather than from this
      // mutation's response. Invalidating the whole cache is heavy-handed for
      // a rename; scoping to the org key would leave a stale name in the
      // switcher until the next natural refetch.
      void queryClient.invalidateQueries();
      setName(updated.name);
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not update the organization. Please try again.",
      );
    },
  });

  /* --- Archive ---------------------------------------------------------- */

  const { mutate: archive, isPending: isArchiving } = useMutation({
    mutationFn: () => archiveOrganization(organizationId),
    onSuccess: () => {
      toast.success(`${organization.organization_name} has been archived.`);
      // Every cached query in the app is now describing an organization the
      // actor can no longer operate in. Clearing rather than invalidating: an
      // invalidate would refetch org-scoped queries that are about to 403 on
      // the way out, producing a burst of error toasts behind the redirect.
      queryClient.clear();
      navigate(ROUTES.WORKSPACES, { replace: true });
    },
    onError: (error: unknown) => {
      // Surfaced verbatim. The server's refusals here are specific --
      // LAST_OWNER, an active subscription, an outstanding balance -- and each
      // tells the actor something they cannot infer from a generic string.
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not archive this organization. Please try again.",
      );
      setConfirmOpen(false);
      setTypedSlug("");
    },
  });

  const slugMatches = typedSlug.trim() === slug;

  /* --- Render ----------------------------------------------------------- */

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
            General
          </h1>
          <StatusPill status={status} />
        </div>
        <p className="text-sm text-muted-foreground">
          Organization profile and lifecycle. These settings apply to every
          workspace and every member.
        </p>
      </header>

      {/* --- Profile ------------------------------------------------------ */}

      <section className="space-y-5 rounded-xl border border-border/60 bg-card/50 p-6 backdrop-blur-sm">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/40">
            <Building2 className="h-4 w-4 text-muted-foreground" />
          </span>
          <div className="min-w-0 space-y-0.5">
            <h2 className="text-sm font-bold text-foreground">
              Organization profile
            </h2>
            <p className="text-xs leading-relaxed text-muted-foreground">
              The name your members see in the workspace switcher and on
              invitations.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label
            htmlFor="org-name"
            className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Organization name
          </label>
          <input
            id="org-name"
            type="text"
            value={name}
            disabled={!canEdit || isArchived}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
          />
        </div>

        <div className="space-y-2">
          <label
            htmlFor="org-slug"
            className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Organization identifier
          </label>
          <input
            id="org-slug"
            type="text"
            value={slug}
            readOnly
            disabled
            className="w-full cursor-not-allowed rounded-lg border border-border bg-muted/30 px-3 py-2 font-mono text-sm text-muted-foreground"
          />
          <p className="text-xs leading-relaxed text-muted-foreground">
            The identifier appears in every URL for this organization. Renaming
            it breaks existing links and bookmarks, so it is changed through
            support rather than here.
          </p>
        </div>

        {canEdit && !isArchived && (
          <div className="flex justify-end">
            <button
              type="button"
              disabled={!nameChanged || isSaving}
              onClick={() => saveProfile()}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Save changes
            </button>
          </div>
        )}

        {!canEdit && (
          <p className="rounded-lg bg-muted/30 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
            Your role in this organization is read-only for these settings.
          </p>
        )}
      </section>

      {/* --- Danger zone -------------------------------------------------- */}

      {canArchive && !isArchived && (
        <section className="space-y-5 rounded-xl border border-destructive/30 bg-destructive/[0.03] p-6">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-destructive/30 bg-destructive/10">
              <AlertTriangle className="h-4 w-4 text-destructive" />
            </span>
            <div className="min-w-0 space-y-0.5">
              <h2 className="text-sm font-bold text-foreground">Danger zone</h2>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Actions here affect every member and every workspace in{" "}
                {organization.organization_name}.
              </p>
            </div>
          </div>

          <div className="space-y-3 rounded-lg border border-border/60 bg-card/60 p-4">
            <h3 className="text-sm font-bold text-foreground">
              Archive organization
            </h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              Archiving deactivates active access, invalidates API keys and
              halts billing, while immutably preserving historical audit logs
              and financial records for compliance.
            </p>
            <ul className="space-y-1.5 text-xs leading-relaxed text-muted-foreground">
              <li className="flex gap-2">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                Audit logs, invoices and usage records are retained in full.
              </li>
              <li className="flex gap-2">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                Members keep their accounts and any other organizations.
              </li>
              <li className="flex gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                Every API key is deactivated immediately. Integrations stop.
              </li>
              <li className="flex gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                Reactivation is a support request, not a button.
              </li>
            </ul>

            {!confirmOpen ? (
              <button
                type="button"
                onClick={() => setConfirmOpen(true)}
                className="inline-flex items-center gap-2 rounded-lg border border-destructive/40 px-4 py-2 text-sm font-semibold text-destructive transition hover:bg-destructive/10"
              >
                <Archive className="h-4 w-4" />
                Archive organization
              </button>
            ) : (
              <div className="space-y-3 border-t border-border/60 pt-4">
                <label
                  htmlFor="confirm-slug"
                  className="block text-xs leading-relaxed text-foreground"
                >
                  Type{" "}
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono font-semibold">
                    {slug}
                  </span>{" "}
                  to confirm.
                </label>
                <input
                  id="confirm-slug"
                  type="text"
                  autoComplete="off"
                  autoFocus
                  value={typedSlug}
                  onChange={(event) => setTypedSlug(event.target.value)}
                  placeholder={slug}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none transition focus:border-destructive focus:ring-2 focus:ring-destructive/20"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={!slugMatches || isArchiving}
                    onClick={() => archive()}
                    className="inline-flex items-center gap-2 rounded-lg bg-destructive px-4 py-2 text-sm font-semibold text-destructive-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {isArchiving ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Archive className="h-4 w-4" />
                    )}
                    {isArchiving ? "Archiving…" : "Archive permanently"}
                  </button>
                  <button
                    type="button"
                    disabled={isArchiving}
                    onClick={() => {
                      setConfirmOpen(false);
                      setTypedSlug("");
                    }}
                    className="rounded-lg border border-border px-4 py-2 text-sm font-semibold text-muted-foreground transition hover:bg-muted/40 hover:text-foreground disabled:opacity-60"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {isArchived && (
        <section className="rounded-xl border border-border/60 bg-muted/20 p-6">
          <p className="text-sm leading-relaxed text-muted-foreground">
            This organization is {status.toLowerCase()}. Its records are
            retained and readable, but it cannot be modified. Reactivation is
            handled through support.
          </p>
        </section>
      )}
    </div>
  );
};

export default OrganizationGeneral;
