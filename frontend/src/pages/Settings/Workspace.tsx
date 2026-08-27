import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { ApiError } from "@/services/api/client";
import { uploadLogo } from "@/services/api/upload";
import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";
import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";

import {
  archiveWorkspace,
  getWorkspaceById,
  leaveWorkspace,
  listWorkspaceMembers,
  restoreWorkspace,
  revokeWorkspaceAccess,
  updateWorkspaceById,
} from "@/services/api/workspaces";

import {
  createInvitation,
  listPendingInvitations,
  resendInvitation,
  revokeInvitation,
} from "@/services/api/invitations";

import { updateOrganization } from "@/services/api/organization";

import { canManageOrganizationSettings, canDeleteWorkspace } from "@/permissions/organizationPermissions";
import {
  canAssignWorkspaceRole,
  canManageWorkspaceMembers,
  canManageWorkspaceSettings,
} from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { ROUTES } from "@/constants/routes";

import type { WorkspaceMember, WorkspaceRole } from "@/types/tenancy";

export const Workspace: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { organization, workspace, organizationRole, workspaceRole, user } =
    useResolvedTenant();

  const workspaceId = workspace.id;
  const organizationId = organization.organization_id;

  const [memberToRemove, setMemberToRemove] = useState<WorkspaceMember | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>("VIEWER");
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const authenticatedLogoSrc = useAuthenticatedImage(logoPreview);

  const canEditWorkspace = canManageWorkspaceSettings(workspaceRole);
  const canEditOrganization = canManageOrganizationSettings(organizationRole);
  const canManageTeam = canManageWorkspaceMembers(workspaceRole);
  const canArchive = canDeleteWorkspace(organizationRole);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<WorkspaceFormData>({
    resolver: zodResolver(workspaceSchema),
    defaultValues: {
      workspace_name: "",
      company_name: "",
      company_logo_url: "",
      timezone: "UTC",
      language: "en",
      currency: "USD",
      date_format: "YYYY-MM-DD",
    },
  });

  const { data: workspaceDetail, isLoading: isLoadingWorkspace } = useQuery({
    queryKey: ["workspaces", "detail", workspaceId],
    queryFn: () => getWorkspaceById(workspaceId),
  });

  const { data: memberList, isLoading: isLoadingMembers } = useQuery({
    queryKey: ["workspaces", "members", workspaceId],
    queryFn: () => listWorkspaceMembers(workspaceId),
  });

  const { data: pendingInvitations, isLoading: isLoadingInvitations } = useQuery({
    queryKey: ["organizations", "invitations", organizationId],
    queryFn: () => listPendingInvitations(organizationId),
    enabled: Boolean(organizationId),
  });

  useEffect(() => {
    if (!workspaceDetail) {
      return;
    }

    reset({
      workspace_name: workspaceDetail.workspace_name,
      company_name: organization.organization_name,
      company_logo_url: workspaceDetail.company_logo_url ?? "",
      timezone: workspaceDetail.timezone as WorkspaceFormData["timezone"],
      language: workspaceDetail.language as WorkspaceFormData["language"],
      currency: workspaceDetail.currency as WorkspaceFormData["currency"],
      date_format: workspaceDetail.date_format as WorkspaceFormData["date_format"],
    });

    setLogoPreview(workspaceDetail.company_logo_url ?? null);
  }, [workspaceDetail, organization.organization_name, reset]);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (isDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  const invalidateTenant = async (): Promise<void> => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["workspaces", "detail", workspaceId] }),
      queryClient.invalidateQueries({ queryKey: ["me"] }),
    ]);
  };

  const { mutateAsync: saveWorkspaceMutation, isPending: isSaving } = useMutation({
    mutationFn: async (data: WorkspaceFormData) => {
      if (
        canEditOrganization &&
        data.company_name !== organization.organization_name
      ) {
        await updateOrganization(organizationId, { name: data.company_name });
      }

      return updateWorkspaceById(workspaceId, {
        workspace_name: data.workspace_name,
        timezone: data.timezone,
        language: data.language,
        currency: data.currency,
        date_format: data.date_format,
      });
    },
    onSuccess: async () => {
      await invalidateTenant();
      reset(undefined, { keepDirty: false });
      toast.success("Workspace settings saved successfully.");
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Failed to save workspace settings.",
      );
    },
  });

  const { mutateAsync: sendInviteMutation, isPending: isInviting } = useMutation({
    mutationFn: (payload: { email: string; role: WorkspaceRole }) =>
      createInvitation(organizationId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["organizations", "invitations", organizationId],
      });
      setInviteEmail("");
      setInviteRole("VIEWER");
      toast.success("Invitation dispatched successfully.");
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to send invitation.",
      );
    },
  });

  const { mutateAsync: revokeInviteMutation } = useMutation({
    mutationFn: (invitationId: string) => revokeInvitation(organizationId, invitationId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["organizations", "invitations", organizationId],
      });
      toast.success("Invitation revoked successfully.");
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to revoke invitation.",
      );
    },
  });

  const { mutateAsync: resendInviteMutation } = useMutation({
    mutationFn: (invitationId: string) => resendInvitation(organizationId, invitationId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["organizations", "invitations", organizationId],
      });
      toast.success("Invitation resent successfully.");
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to resend invitation.",
      );
    },
  });

  const { mutateAsync: removeMemberMutation, isPending: isRemoving } = useMutation({
    mutationFn: async (member: WorkspaceMember) => {
      if (member.user.id === user.id) {
        return leaveWorkspace(workspaceId);
      }

      if (!member.id) {
        throw new ApiError(
          "This member's access comes from their organization role. Change it in organization settings.",
          400,
        );
      }

      return revokeWorkspaceAccess(workspaceId, member.id);
    },
    onSuccess: async (_result, member) => {
      const isSelf = member.user.id === user.id;
      toast.success(isSelf ? "You left the workspace." : "Member removed successfully.");

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", "members", workspaceId] }),
        queryClient.invalidateQueries({ queryKey: ["me"] }),
      ]);

      setMemberToRemove(null);

      if (isSelf) {
        navigate(ROUTES.WORKSPACES, { replace: true });
      }
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to remove member.",
      );
      setMemberToRemove(null);
    },
  });

  const { mutateAsync: archiveMutation, isPending: isArchiving } = useMutation({
    mutationFn: () =>
      workspaceDetail?.status === "ACTIVE"
        ? archiveWorkspace(workspaceId)
        : restoreWorkspace(workspaceId),
    onSuccess: async (updated) => {
      await invalidateTenant();
      setConfirmArchive(false);
      toast.success(
        updated.status === "ACTIVE" ? "Workspace restored." : "Workspace archived.",
      );
      if (updated.status !== "ACTIVE") {
        navigate(ROUTES.WORKSPACES, { replace: true });
      }
    },
    onError: (error: unknown) => {
      setConfirmArchive(false);
      toast.error(
        error instanceof ApiError ? error.message : "Failed to update workspace status.",
      );
    },
  });

  const handleReset = () => {
    if (!workspaceDetail) return;

    reset({
      workspace_name: workspaceDetail.workspace_name,
      company_name: organization.organization_name,
      company_logo_url: workspaceDetail.company_logo_url ?? "",
      timezone: workspaceDetail.timezone as WorkspaceFormData["timezone"],
      language: workspaceDetail.language as WorkspaceFormData["language"],
      currency: workspaceDetail.currency as WorkspaceFormData["currency"],
      date_format: workspaceDetail.date_format as WorkspaceFormData["date_format"],
    });

    setLogoPreview(workspaceDetail.company_logo_url ?? null);
    toast.success("Changes discarded.");
  };

  const handleLogoSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const allowedTypes = ["image/png", "image/jpeg", "image/webp"];
    if (!allowedTypes.includes(file.type)) {
      toast.error("Only PNG, JPEG and WebP images are allowed.");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      toast.error("Logo must be smaller than 2 MB.");
      return;
    }

    try {
      const preview = URL.createObjectURL(file);
      setLogoPreview(preview);
      const response = await uploadLogo(workspaceId, file);
      setLogoPreview(response.logo_url);
      await invalidateTenant();
      toast.success("Logo uploaded successfully.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload logo.");
    } finally {
      event.target.value = "";
    }
  };

  const onSubmit = async (data: WorkspaceFormData): Promise<void> => {
    await saveWorkspaceMutation(data);
  };

  const handleSendInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    await sendInviteMutation({ email: inviteEmail.trim(), role: inviteRole });
  };

  const assignableRoles = useMemo(() => {
    const candidates: WorkspaceRole[] = ["VIEWER", "CONTRIBUTOR", "ADMIN"];
    return candidates.filter((role) =>
      canAssignWorkspaceRole(organizationRole, workspaceRole, role),
    );
  }, [organizationRole, workspaceRole]);

  const members = memberList?.items ?? [];
  const invitations = pendingInvitations ?? [];

  const isPageLoading =
    isLoadingWorkspace || isLoadingMembers || isLoadingInvitations;

  if (isPageLoading) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="space-y-6 animate-pulse">
          <div className="h-8 w-56 rounded bg-muted" />
          <div className="h-4 w-80 rounded bg-muted" />
          <div className="space-y-4 pt-4">
            <div className="space-y-2">
              <div className="h-3 w-24 rounded bg-muted" />
              <div className="h-10 rounded bg-muted" />
            </div>
            <div className="space-y-2">
              <div className="h-3 w-24 rounded bg-muted" />
              <div className="h-10 rounded bg-muted" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  const isArchived = workspaceDetail?.status !== "ACTIVE";

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <h1 className="text-2xl font-bold">Workspace Settings</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Configure your organization's workspace profile and regional preferences.
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          <div className="space-y-2">
            <label htmlFor="workspace_name" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Workspace Name
            </label>
            <input
              id="workspace_name"
              type="text"
              disabled={!canEditWorkspace}
              placeholder="My Workspace"
              required
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
            />
            {errors.workspace_name && (
              <p className="text-xs text-destructive">{errors.workspace_name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <label htmlFor="company_name" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Company Name
            </label>
            <input
              id="company_name"
              type="text"
              disabled={!canEditOrganization}
              placeholder="Company Name"
              required
              {...register("company_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
            />
            {errors.company_name ? (
              <p className="text-xs text-destructive">{errors.company_name.message}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Shared across every workspace in this organization.
                {!canEditOrganization && " Only an organization admin can change it."}
              </p>
            )}
          </div>

          <div className="space-y-4">
            <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Company Logo
            </label>
            <div className="flex items-center gap-4">
              {authenticatedLogoSrc ? (
                <img
                  src={authenticatedLogoSrc}
                  alt="Company logo preview"
                  className="h-20 w-20 rounded-lg border border-border object-cover"
                />
              ) : (
                <div className="flex h-20 w-20 items-center justify-center rounded-lg border border-border text-xs text-muted-foreground bg-muted/20">
                  No Logo
                </div>
              )}

              <div className="flex flex-col gap-2">
                <button
                  type="button"
                  disabled={!canEditWorkspace}
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-muted/50 transition disabled:opacity-50"
                >
                  Upload Logo
                </button>
              </div>

              <input
                ref={fileInputRef}
                className="hidden"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={handleLogoSelect}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="timezone" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Timezone
              </label>
              <select
                id="timezone"
                disabled={!canEditWorkspace}
                {...register("timezone")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="Asia/Kolkata">Asia/Kolkata (IST)</option>
                <option value="Asia/Dubai">Asia/Dubai (GST)</option>
                <option value="Europe/London">Europe/London (GMT/BST)</option>
                <option value="Europe/Berlin">Europe/Berlin (CET)</option>
                <option value="America/New_York">America/New_York (EST)</option>
                <option value="America/Chicago">America/Chicago (CST)</option>
                <option value="America/Los_Angeles">America/Los_Angeles (PST)</option>
                <option value="Asia/Singapore">Asia/Singapore (SGT)</option>
                <option value="Australia/Sydney">Australia/Sydney (AEST)</option>
                <option value="UTC">UTC</option>
              </select>
            </div>

            <div className="space-y-2">
              <label htmlFor="language" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Language
              </label>
              <select
                id="language"
                disabled={!canEditWorkspace}
                {...register("language")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="en">English</option>
                <option value="hi">Hindi</option>
                <option value="ta">Tamil</option>
                <option value="ml">Malayalam</option>
                <option value="te">Telugu</option>
                <option value="kn">Kannada</option>
                <option value="ar">Arabic</option>
                <option value="de">German</option>
                <option value="fr">French</option>
                <option value="es">Spanish</option>
                <option value="ja">Japanese</option>
                <option value="zh">Chinese</option>
              </select>
            </div>

            <div className="space-y-2">
              <label htmlFor="currency" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Currency
              </label>
              <select
                id="currency"
                disabled={!canEditWorkspace}
                {...register("currency")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="INR">Indian Rupee (INR)</option>
                <option value="USD">US Dollar (USD)</option>
                <option value="EUR">Euro (EUR)</option>
                <option value="GBP">British Pound (GBP)</option>
                <option value="AED">UAE Dirham (AED)</option>
                <option value="SGD">Singapore Dollar (SGD)</option>
                <option value="AUD">Australian Dollar (AUD)</option>
                <option value="CAD">Canadian Dollar (CAD)</option>
                <option value="JPY">Japanese Yen (JPY)</option>
                <option value="CNY">Chinese Yuan (CNY)</option>
              </select>
            </div>

            <div className="space-y-2">
              <label htmlFor="date_format" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Date Format
              </label>
              <select
                id="date_format"
                disabled={!canEditWorkspace}
                {...register("date_format")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="DD-MM-YYYY">DD-MM-YYYY</option>
                <option value="MM-DD-YYYY">MM-DD-YYYY</option>
                <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                <option value="YYYY/MM/DD">YYYY/MM/DD</option>
              </select>
            </div>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-border p-4">
            <div>
              <h3 className="font-medium">Workspace Status</h3>
              <p className="text-sm text-muted-foreground">
                {isArchived
                  ? "This workspace is archived. Its data is retained and can be restored."
                  : "Archiving hides this workspace without deleting anything."}
              </p>
            </div>
            <button
              type="button"
              disabled={!canArchive || isArchiving}
              onClick={() => setConfirmArchive(true)}
              className={`rounded-lg border px-4 py-2 text-sm font-medium transition disabled:opacity-50 ${
                isArchived
                  ? "border-border bg-background hover:bg-muted/50"
                  : "border-transparent text-destructive hover:bg-destructive/10"
              }`}
            >
              {isArchived ? "Restore Workspace" : "Archive Workspace"}
            </button>
          </div>

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={handleReset}
              disabled={!isDirty}
              className="rounded-lg border border-border bg-background px-5 py-2 text-sm font-medium hover:bg-muted/50 disabled:opacity-50"
            >
              Reset
            </button>
            <button
              type="submit"
              disabled={!isDirty || isSaving || !canEditWorkspace}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
            >
              {isSaving ? "Saving..." : "Save Workspace"}
            </button>
          </div>
        </form>
      </div>

      <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-4">
        <h2 className="text-xl font-bold">Team Members</h2>
        <p className="text-sm text-muted-foreground">
          View active accounts and access privilege levels within this workspace.
        </p>

        <div className="overflow-x-auto mt-4">
          <table className="w-full text-left text-sm border-collapse border-b border-border">
            <thead>
              <tr className="border-b border-border bg-muted/20 text-muted-foreground text-xs uppercase tracking-wider">
                <th className="py-2.5 px-4 font-bold">Email Address</th>
                <th className="py-2.5 px-4 font-bold">Access Role</th>
                <th className="py-2.5 px-4 font-bold">Status</th>
                <th className="py-2.5 px-4 font-bold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {members.map((mem) => {
                const isSelf = mem.user.id === user.id;
                const isActive = mem.status === "ACTIVE";

                return (
                  <tr
                    key={mem.id ?? `derived-${mem.user.id}`}
                    className="border-b border-border/50 last:border-0 hover:bg-muted/10 transition"
                  >
                    <td className="py-3.5 px-4 font-medium text-foreground">
                      {mem.user.email} {isSelf && "(You)"}
                    </td>
                    <td className="py-3.5 px-4 text-muted-foreground text-xs uppercase font-semibold">
                      {mem.role}
                      {mem.is_derived && (
                        <span className="ml-2 normal-case font-medium text-[10px] text-muted-foreground/70">
                          via {mem.organization_role?.toLowerCase()} role
                        </span>
                      )}
                    </td>
                    <td className="py-3.5 px-4">
                      <span
                        className={`inline-block px-2.5 py-0.5 text-xs font-bold rounded-full ${
                          isActive
                            ? "bg-emerald-100 text-green-800 dark:bg-emerald-900/30 dark:text-green-300"
                            : "bg-destructive/10 text-destructive"
                        }`}
                      >
                        {isActive ? "Active" : mem.status.toLowerCase()}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 text-right">
                      {!mem.is_derived && (canManageTeam || isSelf) && (
                        <button
                          type="button"
                          onClick={() => setMemberToRemove(mem)}
                          className="rounded-lg border border-transparent text-destructive px-3 py-1.5 text-xs font-semibold hover:bg-destructive/10 transition"
                        >
                          {isSelf ? "Leave Workspace" : "Remove Member"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-6">
        <h2 className="text-xl font-bold">Invitations Directory</h2>
        <p className="text-sm text-muted-foreground">
          Invite new collaborators to this workspace or manage active pending invitations.
        </p>

        {canManageTeam && (
          <form onSubmit={handleSendInvite} className="p-4 rounded-lg bg-muted/20 border border-border grid grid-cols-12 gap-4 items-end">
            <div className="col-span-12 md:col-span-6 space-y-2">
              <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Recipient Email
              </label>
              <input
                type="email"
                required
                placeholder="colleague@company.com"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />
            </div>
            <div className="col-span-12 md:col-span-3 space-y-2">
              <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Membership Role
              </label>
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as WorkspaceRole)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
              >
                {assignableRoles.map((role) => (
                  <option key={role} value={role}>
                    {role.charAt(0) + role.slice(1).toLowerCase()}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-span-12 md:col-span-3">
              <button
                type="submit"
                disabled={isInviting || !inviteEmail.trim()}
                className="w-full rounded-lg bg-primary py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
              >
                {isInviting ? "Sending..." : "Send Invite"}
              </button>
            </div>
          </form>
        )}

        <div className="space-y-4">
          <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Active Pending Invites</h3>

          {invitations.length === 0 ? (
            <p className="text-sm text-muted-foreground bg-muted/10 p-4 rounded-lg border border-border/50 text-center">
              No active pending invitations found.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm border-collapse border-b border-border">
                <thead>
                  <tr className="border-b border-border bg-muted/20 text-muted-foreground text-xs uppercase tracking-wider">
                    <th className="py-2.5 px-4 font-bold">Email</th>
                    <th className="py-2.5 px-4 font-bold">Target Role</th>
                    <th className="py-2.5 px-4 font-bold">Expiration Date</th>
                    {canManageTeam && <th className="py-2.5 px-4 font-bold text-right">Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {invitations.map((inv) => {
                    const expired = new Date(inv.expires_at) <= new Date();

                    return (
                      <tr key={inv.id} className="border-b border-border/50 last:border-0 hover:bg-muted/10 transition">
                        <td className="py-3.5 px-4 font-medium text-foreground">{inv.email}</td>
                        <td className="py-3.5 px-4 text-muted-foreground text-xs uppercase font-semibold">{inv.role}</td>
                        <td className="py-3.5 px-4 text-muted-foreground text-xs">
                          {new Date(inv.expires_at).toLocaleString()}
                          {expired && (
                            <span className="ml-2 font-semibold text-destructive">Expired</span>
                          )}
                        </td>
                        {canManageTeam && (
                          <td className="py-3.5 px-4 text-right space-x-2">
                            <button
                              type="button"
                              onClick={() => resendInviteMutation(inv.id)}
                              className="rounded-lg border border-border bg-background px-3 py-1 text-xs font-medium hover:bg-muted/50 transition"
                            >
                              Resend
                            </button>
                            <button
                              type="button"
                              onClick={() => revokeInviteMutation(inv.id)}
                              className="rounded-lg border border-transparent text-destructive px-3 py-1 text-xs font-medium hover:bg-destructive/10 transition"
                            >
                              Revoke
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={memberToRemove !== null}
        title={memberToRemove?.user.id === user.id ? "Leave Workspace" : "Remove Workspace Member"}
        message={
          memberToRemove?.user.id === user.id
            ? "Are you sure you want to leave this workspace? You will no longer have access to its resources."
            : `Are you sure you want to remove ${memberToRemove?.user.email ?? "this member"} from the workspace?`
        }
        confirmText={memberToRemove?.user.id === user.id ? "Leave" : "Remove"}
        cancelText="Cancel"
        loading={isRemoving}
        onCancel={() => setMemberToRemove(null)}
        onConfirm={() => {
          if (memberToRemove) {
            removeMemberMutation(memberToRemove);
          }
        }}
      />

      <ConfirmDialog
        open={confirmArchive}
        title={isArchived ? "Restore Workspace" : "Archive Workspace"}
        message={
          isArchived
            ? "Restore this workspace and make it accessible again?"
            : "Archive this workspace? Its documents and settings are retained and it can be restored later."
        }
        confirmText={isArchived ? "Restore" : "Archive"}
        cancelText="Cancel"
        loading={isArchiving}
        onCancel={() => setConfirmArchive(false)}
        onConfirm={() => {
          void archiveMutation();
        }}
      />
    </div>
  );
};

export default Workspace;
