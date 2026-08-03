import React, { useEffect, useRef, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getWorkspace,
  saveWorkspace,
  listWorkspaceMembers,
  listPendingInvitations,
  inviteUser,
  resendInvitation,
  revokeInvitation,
  getMyMembership,
} from "@/services/api/workspace";

import { uploadLogo } from "@/services/api/upload";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";

import type { WorkspaceRole } from "@/types/workspace";

const API_BASE_URL = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"
).replace("/api/v1", "");

export const Workspace: React.FC = () => {
  const {
    register,
    handleSubmit,
    reset,
    setValue,
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

      is_active: true,
    },
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [selectedLogoFile, setSelectedLogoFile] = useState<File | null>(null);

  // Form States for Invitations
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>("VIEWER");

  const queryClient = useQueryClient();

  // Queries
  const { data: workspace, isLoading: isLoadingWorkspace } = useQuery({
    queryKey: ["workspace"],
    queryFn: getWorkspace,
  });

  const { data: members, isLoading: isLoadingMembers } = useQuery({
    queryKey: ["workspace_members"],
    queryFn: listWorkspaceMembers,
  });

  const { data: pendingInvitations, isLoading: isLoadingInvitations } = useQuery({
    queryKey: ["workspace_invitations_pending"],
    queryFn: listPendingInvitations,
  });

  const { data: myMembership } = useQuery({
    queryKey: ["workspace_membership_me"],
    queryFn: getMyMembership,
  });

  useEffect(() => {
    if (!workspace) {
      return;
    }

    reset({
      workspace_name: workspace.workspace_name,
      company_name: workspace.company_name,
      company_logo_url: workspace.company_logo_url ?? "",

      timezone: workspace.timezone,
      language: workspace.language,
      currency: workspace.currency,
      date_format: workspace.date_format,

      is_active: workspace.is_active,
    });
  }, [workspace, reset]);

  useEffect(() => {
    if (!workspace) return;

    setLogoPreview(workspace.company_logo_url ?? null);
  }, [workspace]);

  // Prevent accidental tab closure if form is dirty
  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty) return;

      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, [isDirty]);

  // Mutations
  const { mutateAsync: saveWorkspaceMutation, isPending: isSaving } =
    useMutation({
      mutationFn: saveWorkspace,

      onSuccess: async () => {
        await queryClient.invalidateQueries({
          queryKey: ["workspace"],
        });

        setSelectedLogoFile(null);

        reset(undefined, {
          keepDirty: false,
        });

        toast.success("Workspace settings saved successfully.");
      },

      onError: (error: unknown) => {
        if (error instanceof Error) {
          toast.error(error.message);
          return;
        }

        toast.error("Failed to save workspace settings.");
      },
    });

  const { mutateAsync: sendInviteMutation, isPending: isInviting } = useMutation({
    mutationFn: inviteUser,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["workspace_invitations_pending"],
      });
      setInviteEmail("");
      setInviteRole("VIEWER");
      toast.success("Invitation dispatched successfully.");
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to send invitation.");
    },
  });

  const { mutateAsync: revokeInviteMutation } = useMutation({
    mutationFn: revokeInvitation,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["workspace_invitations_pending"],
      });
      toast.success("Invitation revoked successfully.");
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to revoke invitation.");
    },
  });

  const { mutateAsync: resendInviteMutation } = useMutation({
    mutationFn: resendInvitation,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["workspace_invitations_pending"],
      });
      toast.success("Invitation resent successfully.");
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to resend invitation.");
    },
  });

  const handleReset = async () => {
    if (!workspace) return;

    reset({
      workspace_name: workspace.workspace_name,
      company_name: workspace.company_name,
      company_logo_url: workspace.company_logo_url ?? "",
      timezone: workspace.timezone,
      language: workspace.language,
      currency: workspace.currency,
      date_format: workspace.date_format,
      is_active: workspace.is_active,
    });

    setSelectedLogoFile(null);

    setLogoPreview(workspace.company_logo_url ?? null);

    toast.success("Changes discarded.");
  };

  const handleLogoSelect = (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];

    if (!file) return;

    const allowedTypes = [
      "image/png",
      "image/jpeg",
      "image/webp",
    ];

    if (!allowedTypes.includes(file.type)) {
      toast.error("Only PNG, JPEG and WebP images are allowed.");
      return;
    }

    if (file.size > 2 * 1024 * 1024) {
      toast.error("Logo must be smaller than 2 MB.");
      return;
    }

    setSelectedLogoFile(file);

    const preview = URL.createObjectURL(file);

    setLogoPreview(preview);

    setValue("company_logo_url", preview, {
      shouldDirty: true,
    });

    event.target.value = "";
  };

  const onSubmit = async (
    data: WorkspaceFormData
  ): Promise<void> => {
    try {
      let logoUrl = data.company_logo_url;

      if (selectedLogoFile) {
        const response = await uploadLogo(selectedLogoFile);
        logoUrl = response.logo_url;
      }

      await saveWorkspaceMutation({
        ...data,
        company_logo_url: logoUrl || null,
      });

      setSelectedLogoFile(null);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save workspace."
      );
    }
  };

  const handleSendInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    await sendInviteMutation({ email: inviteEmail, role: inviteRole });
  };

  const isPageLoading = isLoadingWorkspace || isLoadingMembers || isLoadingInvitations;

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

  // Multi-tenant RBAC checks: only Owner and Manager accounts possess administrative write authority
  const canManageTeam = myMembership?.role === "OWNER" || myMembership?.role === "MANAGER";

  return (
    <div className="space-y-6">
      {/* 1. Branding Settings Card */}
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <h1 className="text-2xl font-bold">Workspace Settings</h1>

        <p className="mt-2 text-sm text-muted-foreground">
          Configure your organization's workspace profile and regional
          preferences.
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-6">
          {/* Workspace Name */}
          <div className="space-y-2">
            <label
              htmlFor="workspace_name"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              Workspace Name
            </label>

            <input
              id="workspace_name"
              type="text"
              disabled={!canManageTeam}
              placeholder="My Workspace"
              required
              aria-required="true"
              aria-invalid={!!errors.workspace_name}
              aria-describedby={
                errors.workspace_name ? "workspace-name-error" : undefined
              }
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
            />

            {errors.workspace_name && (
              <p
                id="workspace-name-error"
                role="alert"
                className="text-xs text-destructive"
              >
                {errors.workspace_name.message}
              </p>
            )}
          </div>

          {/* Company Name */}
          <div className="space-y-2">
            <label
              htmlFor="company_name"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              Company Name
            </label>

            <input
              id="company_name"
              type="text"
              disabled={!canManageTeam}
              placeholder="Workspace Name"
              required
              aria-required="true"
              aria-invalid={!!errors.company_name}
              aria-describedby={
                errors.company_name ? "company-name-error" : undefined
              }
              {...register("company_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
            />

            {errors.company_name && (
              <p
                id="company-name-error"
                role="alert"
                className="text-xs text-destructive"
              >
                {errors.company_name.message}
              </p>
            )}
          </div>

          {/* Company Logo Upload */}
          <div className="space-y-4">
            <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Company Logo
            </label>

            <div className="flex items-center gap-4">
              {logoPreview ? (
                <img
                  src={
                    logoPreview?.startsWith("blob:")
                      ? logoPreview
                      : logoPreview
                        ? `${API_BASE_URL}${logoPreview}`
                        : undefined
                  }
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
                  disabled={!canManageTeam}
                  aria-label="Upload company logo"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-muted/50 transition disabled:opacity-50"
                >
                  Upload Logo
                </button>

                {logoPreview && canManageTeam && (
                  <button
                    type="button"
                    onClick={async () => {
                      setSelectedLogoFile(null);

                      setLogoPreview(null);

                      setValue("company_logo_url", "", {
                        shouldDirty: true,
                        shouldValidate: true,
                      });
                    }}
                    className="rounded-lg border border-transparent text-destructive px-4 py-2 text-sm font-medium hover:bg-destructive/10 transition text-left"
                  >
                    Remove Logo
                  </button>
                )}
              </div>

              <input
                id="company_logo"
                ref={fileInputRef}
                className="hidden"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={handleLogoSelect}
                aria-hidden="true"
                tabIndex={-1}
              />
              <input type="hidden" {...register("company_logo_url")} />
            </div>
          </div>

          {/* Regional Settings */}
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* Timezone */}
            <div className="space-y-2">
              <label
                htmlFor="timezone"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Timezone
              </label>

              <select
                id="timezone"
                disabled={!canManageTeam}
                aria-invalid={!!errors.timezone}
                aria-describedby={
                  errors.timezone ? "timezone-error" : undefined
                }
                {...register("timezone")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="Asia/Kolkata">Asia/Kolkata (IST)</option>
                <option value="Asia/Dubai">Asia/Dubai (GST)</option>
                <option value="Europe/London">Europe/London (GMT/BST)</option>
                <option value="Europe/Berlin">Europe/Berlin (CET)</option>
                <option value="America/New_York">America/New_York (EST)</option>
                <option value="America/Chicago">America/Chicago (CST)</option>
                <option value="America/Los_Angeles">
                  America/Los_Angeles (PST)
                </option>
                <option value="Asia/Singapore">Asia/Singapore (SGT)</option>
                <option value="Australia/Sydney">
                  Australia/Sydney (AEST)
                </option>
                <option value="UTC">UTC</option>
              </select>

              {errors.timezone && (
                <p
                  id="timezone-error"
                  role="alert"
                  className="text-xs text-destructive"
                >
                  {errors.timezone.message}
                </p>
              )}
            </div>

            {/* Language */}
            <div className="space-y-2">
              <label
                htmlFor="language"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Language
              </label>

              <select
                id="language"
                disabled={!canManageTeam}
                aria-invalid={!!errors.language}
                aria-describedby={
                  errors.language ? "language-error" : undefined
                }
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

              {errors.language && (
                <p
                  id="language-error"
                  role="alert"
                  className="text-xs text-destructive"
                >
                  {errors.language.message}
                </p>
              )}
            </div>

            {/* Currency */}
            <div className="space-y-2">
              <label
                htmlFor="currency"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Currency
              </label>

              <select
                id="currency"
                disabled={!canManageTeam}
                aria-invalid={!!errors.currency}
                aria-describedby={
                  errors.currency ? "currency-error" : undefined
                }
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

              {errors.currency && (
                <p
                  id="currency-error"
                  role="alert"
                  className="text-xs text-destructive"
                >
                  {errors.currency.message}
                </p>
              )}
            </div>

            {/* Date Format */}
            <div className="space-y-2">
              <label
                htmlFor="date_format"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Date Format
              </label>

              <select
                id="date_format"
                disabled={!canManageTeam}
                aria-invalid={!!errors.date_format}
                aria-describedby={
                  errors.date_format ? "date-format-error" : undefined
                }
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

              {errors.date_format && (
                <p
                  id="date-format-error"
                  role="alert"
                  className="text-xs text-destructive"
                >
                  {errors.date_format.message}
                </p>
              )}
            </div>
          </div>

          {/* Workspace Active */}
          <div className="flex items-center justify-between rounded-lg border border-border p-4">
            <div>
              <h3 className="font-medium">Workspace Active</h3>

              <p className="text-sm text-muted-foreground">
                Enable or disable this workspace.
              </p>
            </div>

            <input
              id="is_active"
              type="checkbox"
              disabled={!canManageTeam}
              aria-label="Workspace Active"
              {...register("is_active")}
              className="h-5 w-5 disabled:opacity-50"
            />
          </div>

          {/* Action Buttons */}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              aria-label="Reset workspace changes"
              onClick={handleReset}
              disabled={!isDirty || !canManageTeam}
              className="rounded-lg border border-border bg-background px-5 py-2 text-sm font-medium text-foreground transition hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset
            </button>

            <button
              type="submit"
              disabled={!isDirty || isSaving || !canManageTeam}
              className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? "Saving..." : "Save Workspace"}
            </button>
          </div>
        </form>

        {Object.keys(errors).length > 0 && (
          <p className="mt-4 text-sm text-destructive">Validation is active.</p>
        )}
      </div>

      {/* 2. Team Directory Table Card */}
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
              </tr>
            </thead>
            <tbody>
              {members?.map((mem) => (
                <tr key={mem.id} className="border-b border-border/50 last:border-0 hover:bg-muted/10 transition">
                  <td className="py-3.5 px-4 font-medium text-foreground">
                    {mem.user?.email || "Unknown User"}
                  </td>
                  <td className="py-3.5 px-4 text-muted-foreground text-xs uppercase font-semibold">
                    {mem.role}
                  </td>
                  <td className="py-3.5 px-4">
                    <span
                      className={`inline-block px-2.5 py-0.5 text-xs font-bold rounded-full ${
                        mem.is_active
                          ? "bg-emerald-100 text-green-800 dark:bg-emerald-900/30 dark:text-green-300"
                          : "bg-destructive/10 text-destructive"
                      }`}
                    >
                      {mem.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 3. Outbound / Pending Invitations Card */}
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-6">
        <h2 className="text-xl font-bold">Invitations Directory</h2>
        <p className="text-sm text-muted-foreground">
          Invite new collaborators to this workspace or manage active pending invitations.
        </p>

        {/* Inline Invite Form */}
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
                <option value="VIEWER">Viewer</option>
                <option value="CONTRIBUTOR">Contributor</option>
                <option value="MANAGER">Manager</option>
              </select>
            </div>
            <div className="col-span-12 md:col-span-3">
              <button
                type="submit"
                disabled={isInviting || !inviteEmail.trim()}
                className="w-full rounded-lg bg-primary py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isInviting ? "Sending..." : "Send Invite"}
              </button>
            </div>
          </form>
        )}

        {/* Pending Invites List */}
        <div className="space-y-4">
          <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Active Pending Invites</h3>

          {!pendingInvitations || pendingInvitations.invitations.length === 0 ? (
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
                  {pendingInvitations.invitations.map((inv) => (
                    <tr key={inv.id} className="border-b border-border/50 last:border-0 hover:bg-muted/10 transition">
                      <td className="py-3.5 px-4 font-medium text-foreground">{inv.email}</td>
                      <td className="py-3.5 px-4 text-muted-foreground text-xs uppercase font-semibold">{inv.role}</td>
                      <td className="py-3.5 px-4 text-muted-foreground text-xs">
                        {new Date(inv.expires_at).toLocaleString()}
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
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Workspace;
