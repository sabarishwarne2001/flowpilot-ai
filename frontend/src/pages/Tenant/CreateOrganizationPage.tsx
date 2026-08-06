/**
 * Tenant provisioning page for FlowPilot AI.
 *
 * Replaces pages/Auth/OnboardingPage.tsx, which called PUT /workspace — a
 * single endpoint that both created and updated. That conflation is audit
 * blocker B7: an existing owner who revisited the onboarding screen silently
 * overwrote their live workspace name, company, timezone, and currency with
 * the form's defaults.
 *
 * The backend half of that fix was deleting the endpoint. The frontend half is
 * the redirect below: an actor who already belongs to an organization is sent
 * to their workspace and never sees this form. Creation and update are now
 * separate operations reached from separate places.
 *
 * Provisioning is atomic on the server — organization, first workspace, and
 * both memberships commit together or not at all. There is no partial state to
 * recover from here.
 */

import React, { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Navigate, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Check, Loader2, X } from "lucide-react";

import { API_ERROR_CODES } from "@/constants/errorCodes";
import { meContextQueryKey } from "@/hooks/useMeContext";
import { useTenant } from "@/hooks/useTenant";
import {
  createOrganizationSchema,
  deriveSlug,
  MIN_SLUG_LENGTH,
  type CreateOrganizationFormData,
} from "@/schemas/organization";
import { workspacePath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";
import { getMeContext } from "@/services/api/me";
import {
  checkOrganizationSlug,
  createOrganization,
} from "@/services/api/organization";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * Debounces a value.
 *
 * The slug availability check runs on every keystroke otherwise, which is a
 * request per character for a result the user cannot act on until they stop
 * typing.
 */
const useDebouncedValue = <T,>(value: T, delayMs: number): T => {
  const [debounced, setDebounced] = useState<T>(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
};

export const CreateOrganizationPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const userId = useAuthStore((store) => store.user?.id ?? null);
  const { state } = useTenant();

  const [slugTouched, setSlugTouched] = useState(false);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<CreateOrganizationFormData>({
    resolver: zodResolver(createOrganizationSchema),
    mode: "onChange",
    defaultValues: {
      organization_name: "",
      workspace_name: "",
      organization_slug: "",
    },
  });

  const organizationName = watch("organization_name");
  const slug = watch("organization_slug");

  // Mirror the name into the slug until the user edits the slug directly.
  // After that the slug is theirs and is never overwritten — silently changing
  // a field someone has typed into is a reliable way to lose their input.
  useEffect(() => {
    if (slugTouched) {
      return;
    }
    setValue("organization_slug", deriveSlug(organizationName ?? ""), {
      shouldValidate: false,
    });
  }, [organizationName, slugTouched, setValue]);

  const debouncedSlug = useDebouncedValue(slug, 400);

  const { data: availability, isFetching: isCheckingSlug } = useQuery({
    queryKey: ["organization", "slug-available", debouncedSlug],
    queryFn: () => checkOrganizationSlug(debouncedSlug),
    enabled: debouncedSlug.length >= MIN_SLUG_LENGTH && !errors.organization_slug,
    retry: false,
    staleTime: 30_000,
  });

  const { mutateAsync: provision, isPending } = useMutation({
    mutationFn: createOrganization,
  });

  const onSubmit = async (data: CreateOrganizationFormData): Promise<void> => {
    try {
      const organization = await provision({
        organization_name: data.organization_name,
        organization_slug: data.organization_slug,
        ...(data.workspace_name ? { workspace_name: data.workspace_name } : {}),
      });

      // POST /organizations returns the organization only, not the workspace
      // created alongside it. Refetching the bootstrap context is how the
      // destination is resolved — and keeps /me/context the single answer to
      // "where can this user go" rather than adding a second one here.
      await queryClient.invalidateQueries({ queryKey: ["me"] });

      const context = await queryClient.fetchQuery({
        queryKey: meContextQueryKey(userId),
        queryFn: getMeContext,
      });

      const created = context.organizations.find(
        (candidate) => candidate.organization_id === organization.id,
      );
      const workspace = created?.workspaces[0];

      toast.success(`${organization.name} is ready.`);

      if (created && workspace) {
        navigate(workspacePath(created.organization_slug, workspace.slug), {
          replace: true,
        });
        return;
      }

      // Provisioning succeeded but the context has not caught up. Sending the
      // user to the picker is honest; re-submitting the form would create a
      // second organization.
      navigate("/workspaces", { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        if (
          error.code === API_ERROR_CODES.ORGANIZATION_ALREADY_EXISTS ||
          error.code === API_ERROR_CODES.SLUG_UNAVAILABLE ||
          error.code === API_ERROR_CODES.SLUG_RESERVED
        ) {
          toast.error(error.message);
          setSlugTouched(true);
          return;
        }
        toast.error(error.message);
        return;
      }
      toast.error("Unable to create your organization. Please try again.");
    }
  };

  const slugStatus = useMemo(() => {
    if (!slug || slug.length < MIN_SLUG_LENGTH || errors.organization_slug) {
      return null;
    }
    if (isCheckingSlug) {
      return "checking" as const;
    }
    if (availability?.available === true) {
      return "available" as const;
    }
    if (availability?.available === false) {
      return "taken" as const;
    }
    return null;
  }, [slug, errors.organization_slug, isCheckingSlug, availability]);

  // Blocker B7, frontend half. An actor who already belongs to an organization
  // must never reach this form.
  if (state.status === "ready") {
    return (
      <Navigate
        to={workspacePath(
          state.organization.organization_slug,
          state.workspace.slug,
        )}
        replace
      />
    );
  }

  if (state.status === "no_workspace") {
    return <Navigate to="/workspaces" replace />;
  }

  if (state.status === "loading") {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const busy = isSubmitting || isPending;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-md space-y-8">
        <header className="space-y-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10">
            <Building2 className="h-5 w-5 text-primary" />
          </div>
          <div className="space-y-1">
            <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
              Create your organization
            </h1>
            <p className="text-sm font-medium leading-relaxed text-muted-foreground">
              Your organization holds your team, your billing, and your
              workspaces. You can add more workspaces at any time.
            </p>
          </div>
        </header>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
          <div className="space-y-2">
            <label
              htmlFor="organization_name"
              className="text-sm font-semibold text-foreground"
            >
              Organization name
            </label>
            <input
              id="organization_name"
              type="text"
              autoComplete="organization"
              placeholder="Acme Inc."
              disabled={busy}
              {...register("organization_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary disabled:opacity-60"
            />
            {errors.organization_name && (
              <p className="text-xs font-medium text-destructive">
                {errors.organization_name.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <label
              htmlFor="organization_slug"
              className="text-sm font-semibold text-foreground"
            >
              URL identifier
            </label>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 transition focus-within:border-primary">
              <span className="shrink-0 text-sm text-muted-foreground">/</span>
              <input
                id="organization_slug"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="acme-inc"
                disabled={busy}
                {...register("organization_slug", {
                  onChange: () => setSlugTouched(true),
                })}
                className="w-full bg-transparent text-sm outline-none disabled:opacity-60"
              />
              {slugStatus === "checking" && (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
              )}
              {slugStatus === "available" && (
                <Check className="h-4 w-4 shrink-0 text-emerald-500" />
              )}
              {slugStatus === "taken" && (
                <X className="h-4 w-4 shrink-0 text-destructive" />
              )}
            </div>
            {errors.organization_slug ? (
              <p className="text-xs font-medium text-destructive">
                {errors.organization_slug.message}
              </p>
            ) : slugStatus === "taken" ? (
              <p className="text-xs font-medium text-destructive">
                {availability?.reason ?? "This identifier is already taken."}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                This becomes part of your workspace URLs.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <label
              htmlFor="workspace_name"
              className="text-sm font-semibold text-foreground"
            >
              First workspace{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </label>
            <input
              id="workspace_name"
              type="text"
              autoComplete="off"
              placeholder="General"
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

          <button
            type="submit"
            disabled={busy || slugStatus === "taken"}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {busy ? "Creating..." : "Create organization"}
          </button>
        </form>
      </div>
    </div>
  );
};

export default CreateOrganizationPage;
