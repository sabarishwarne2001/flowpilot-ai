import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, LayoutGrid } from "lucide-react";
import { z } from "zod";

import { ROUTES } from "@/constants/routes";
import { meContextQueryKey } from "@/hooks/useMeContext";
import { useTenant } from "@/hooks/useTenant";
import { canCreateWorkspace } from "@/permissions/organizationPermissions";
import { deriveSlug, MAX_SLUG_LENGTH, MIN_SLUG_LENGTH, SLUG_PATTERN } from "@/schemas/organization";
import { workspacePath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";
import { getMeContext } from "@/services/api/me";
import { createWorkspace } from "@/services/api/organization";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * Workspace creation page for FlowPilot AI.
 *
 * The missing half of the multi-workspace story. POST
 * /organizations/{id}/workspaces has existed since ARCH-01 backend Step 9b,
 * but nothing in the UI reached it — so the switcher, which only appears with
 * more than one workspace, could never appear.
 *
 * Namespaced under /organizations/{orgSlug}/ rather than under a tenant path,
 * for two reasons: creating a workspace is governed by ORGANIZATION role, not
 * workspace role, and the page must be reachable by an actor who has no
 * workspace to be inside — so it cannot mount under TenantGuard.
 *
 * Distinct from CreateOrganizationPage. That founds a tenant, which is an
 * account-level capability open to any authenticated user. This adds a
 * collaboration boundary inside an existing tenant, and its parent
 * organization decides who may do it.
 */

const createWorkspaceSchema = z.object({
  workspace_name: z
    .string()
    .trim()
    .min(1, "Workspace name is required.")
    .max(100, "Workspace name cannot exceed 100 characters."),

  slug: z
    .string()
    .trim()
    .min(MIN_SLUG_LENGTH, `Identifier must be at least ${MIN_SLUG_LENGTH} characters.`)
    .max(MAX_SLUG_LENGTH, `Identifier cannot exceed ${MAX_SLUG_LENGTH} characters.`)
    .regex(SLUG_PATTERN, "Use lowercase letters, numbers, and single hyphens between them."),
});

type CreateWorkspaceFormData = z.infer<typeof createWorkspaceSchema>;

export const CreateWorkspacePage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { orgSlug } = useParams<{ orgSlug: string }>();

  const userId = useAuthStore((store) => store.user?.id ?? null);
  const { state } = useTenant();

  const [slugTouched, setSlugTouched] = useState(false);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<CreateWorkspaceFormData>({
    resolver: zodResolver(createWorkspaceSchema),
    mode: "onChange",
    defaultValues: { workspace_name: "", slug: "" },
  });

  const workspaceName = watch("workspace_name");

  // Mirror the name into the slug until the user edits the slug directly.
  // After that it is theirs and is never overwritten.
  useEffect(() => {
    if (slugTouched) {
      return;
    }
    setValue("slug", deriveSlug(workspaceName ?? ""), { shouldValidate: false });
  }, [workspaceName, slugTouched, setValue]);

  const organizations =
    state.status === "ready" || state.status === "no_workspace"
      ? state.organizations
      : [];

  const organization = organizations.find(
    (candidate) => candidate.organization_slug === orgSlug,
  );

  const { mutateAsync: provision, isPending } = useMutation({
    mutationFn: (data: CreateWorkspaceFormData) =>
      createWorkspace(organization?.organization_id as string, {
        workspace_name: data.workspace_name,
        slug: data.slug,
      }),
  });

  const onSubmit = async (data: CreateWorkspaceFormData): Promise<void> => {
    if (!organization) {
      return;
    }

    try {
      const created = await provision(data);

      // The bootstrap context now holds a workspace it did not before, and it
      // is what the switcher, the sidebar, and TenantGuard all read. Refetching
      // before navigating means the guard resolves against current data rather
      // than bouncing the user back for a tenant it cannot yet see.
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      await queryClient.fetchQuery({
        queryKey: meContextQueryKey(userId),
        queryFn: getMeContext,
      });

      toast.success(`${created.workspace_name} is ready.`);
      navigate(workspacePath(organization.organization_slug, created.slug), {
        replace: true,
      });
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Unable to create the workspace. Please try again.",
      );
      setSlugTouched(true);
    }
  };

  if (state.status === "loading") {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (state.status === "onboarding_required") {
    return <Navigate to={ROUTES.ONBOARDING} replace />;
  }

  // The organization is unreachable, or the actor's role does not permit
  // creating workspaces in it. Both resolve to the picker rather than a
  // permission error: a dead end here would leave them with nowhere to go.
  if (!organization || !canCreateWorkspace(organization.role)) {
    return <Navigate to={ROUTES.WORKSPACES} replace />;
  }

  const busy = isSubmitting || isPending;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-md space-y-8">
        <button
          type="button"
          onClick={() => navigate(ROUTES.WORKSPACES)}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground transition hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All workspaces
        </button>

        <header className="space-y-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10">
            <LayoutGrid className="h-5 w-5 text-primary" />
          </div>
          <div className="space-y-1">
            <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
              Create a workspace
            </h1>
            <p className="text-sm font-medium leading-relaxed text-muted-foreground">
              A new collaboration space inside{" "}
              <strong className="text-foreground">
                {organization.organization_name}
              </strong>
              . It shares your team and billing, with its own documents and
              settings.
            </p>
          </div>
        </header>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
          <div className="space-y-2">
            <label
              htmlFor="workspace_name"
              className="text-sm font-semibold text-foreground"
            >
              Workspace name
            </label>
            <input
              id="workspace_name"
              type="text"
              autoComplete="off"
              placeholder="Engineering"
              disabled={busy}
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary disabled:opacity-60"
            />
            {errors.workspace_name && (
              <p className="text-xs font-medium text-destructive">
                {errors.workspace_name.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <label htmlFor="slug" className="text-sm font-semibold text-foreground">
              URL identifier
            </label>
            <div className="flex items-center gap-1 rounded-lg border border-border bg-background px-3 py-2 transition focus-within:border-primary">
              <span className="shrink-0 text-sm text-muted-foreground">
                /{organization.organization_slug}/
              </span>
              <input
                id="slug"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="engineering"
                disabled={busy}
                {...register("slug", { onChange: () => setSlugTouched(true) })}
                className="w-full bg-transparent text-sm outline-none disabled:opacity-60"
              />
            </div>
            {errors.slug ? (
              <p className="text-xs font-medium text-destructive">
                {errors.slug.message}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Unique within this organization. Another organization may use
                the same identifier.
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {busy ? "Creating..." : "Create workspace"}
          </button>
        </form>
      </div>
    </div>
  );
};

export default CreateWorkspacePage;
