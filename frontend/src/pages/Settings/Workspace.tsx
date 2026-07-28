import React, { useEffect, useRef, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getWorkspace, saveWorkspace } from "@/services/api/workspace";

import { uploadLogo, deleteLogo } from "@/services/api/upload";

import { ApiError } from "@/services/api/client";

import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";

const API_BASE_URL = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"
).replace("/api/v1", "");

export const Workspace: React.FC = () => {
  const {
    register,
    control,
    handleSubmit,
    reset,
    watch,
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

      primary_color: "#2563EB",
      secondary_color: "#0F172A",

      is_active: true,
    },
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [uploadedLogo, setUploadedLogo] = useState<string | null>(null);

  const previewName = watch("workspace_name");
  const previewPrimary = watch("primary_color");
  const previewSecondary = watch("secondary_color");
  const previewLogo = logoPreview ? `${API_BASE_URL}${logoPreview}` : null;

  const { data: workspace, isLoading: isLoadingWorkspace } = useQuery({
    queryKey: ["workspace"],
    queryFn: getWorkspace,
  });

  const queryClient = useQueryClient();

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

      primary_color: workspace.primary_color,
      secondary_color: workspace.secondary_color,

      is_active: workspace.is_active,
    });
  }, [workspace, reset]);

  useEffect(() => {
    if (!workspace) return;

    setLogoPreview(workspace.company_logo_url ?? null);
    setUploadedLogo(null);
  }, [workspace]);

  // Prevent accidental tab closure or browser refresh if form has unsaved changes
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

  const handleReset = async () => {
    if (!workspace) return;

    if (uploadedLogo && uploadedLogo !== workspace.company_logo_url) {
      try {
        await deleteLogo(uploadedLogo);
      } catch {}
    }

    setUploadedLogo(null);

    reset({
      workspace_name: workspace.workspace_name,
      company_name: workspace.company_name,
      company_logo_url: workspace.company_logo_url ?? "",
      timezone: workspace.timezone,
      language: workspace.language,
      currency: workspace.currency,
      date_format: workspace.date_format,
      primary_color: workspace.primary_color,
      secondary_color: workspace.secondary_color,
      is_active: workspace.is_active,
    });

    setLogoPreview(workspace.company_logo_url ?? null);

    toast.success("Changes discarded.");
  };

  const handleLogoSelect = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
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
      if (uploadedLogo) {
        try {
          await deleteLogo(uploadedLogo);
        } catch {}
      }

      const response = await uploadLogo(file);

      setUploadedLogo(response.logo_url);

      setLogoPreview(response.logo_url);

      setValue("company_logo_url", response.logo_url, {
        shouldDirty: true,
      });

      toast.success("Logo uploaded successfully.");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Logo upload failed."
      );
    } finally {
      event.target.value = "";
    }
  };

  const { mutateAsync: saveWorkspaceMutation, isPending: isSaving } =
    useMutation({
      mutationFn: saveWorkspace,

      onMutate: async (updatedWorkspace) => {
        // Cancel any outgoing refetches so they do not overwrite our optimistic update
        await queryClient.cancelQueries({
          queryKey: ["workspace"],
        });

        // Snapshot the previous workspace state
        const previousWorkspace = queryClient.getQueryData(["workspace"]);

        // Optimistically update the query cache with new values
        queryClient.setQueryData(["workspace"], (old: any) => ({
          ...old,
          ...updatedWorkspace,
        }));

        // Return context containing previous state for rollback on error
        return { previousWorkspace };
      },

      onSuccess: async () => {
        await queryClient.invalidateQueries({
          queryKey: ["workspace"],
          exact: false,
        });
        setUploadedLogo(null);

        reset(
          {
            ...watch(),
          },
          {
            keepDirty: false,
          }
        );

        toast.success("Workspace settings saved successfully.");
      },

      onError: (error: unknown, _variables, context) => {
        // Rollback to the cached snapshotted value if the mutation fails
        if (context?.previousWorkspace) {
          queryClient.setQueryData(["workspace"], context.previousWorkspace);
        }

        if (error instanceof ApiError) {
          toast.error(error.message);
          return;
        }

        toast.error("Failed to save workspace settings.");
      },
    });

  const onSubmit = async (data: WorkspaceFormData): Promise<void> => {
    await saveWorkspaceMutation({
      ...data,
      company_logo_url: data.company_logo_url || null,
    });
  };

  const getInitials = (name: string) => {
    return (name || "FP")
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word.charAt(0).toUpperCase())
      .join("");
  };

  if (isLoadingWorkspace) {
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

            <div className="grid grid-cols-2 gap-6">
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
      </div>
    );
  }

  return (
    <div className="space-y-6">
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
              placeholder="My Workspace"
              required
              aria-required="true"
              aria-invalid={!!errors.workspace_name}
              aria-describedby={
                errors.workspace_name ? "workspace-name-error" : undefined
              }
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
              placeholder="Workspace Name"
              required
              aria-required="true"
              aria-invalid={!!errors.company_name}
              aria-describedby={
                errors.company_name ? "company-name-error" : undefined
              }
              {...register("company_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
                  src={previewLogo ?? undefined}
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
                  aria-label="Upload company logo"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-muted/50 transition"
                >
                  Upload Logo
                </button>

                {logoPreview && (
                  <button
                    type="button"
                    onClick={async () => {
                      const logoToDelete = uploadedLogo ?? logoPreview;

                      if (logoToDelete) {
                        try {
                          await deleteLogo(logoToDelete);
                        } catch {}
                      }

                      setUploadedLogo(null);

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
                aria-invalid={!!errors.timezone}
                aria-describedby={
                  errors.timezone ? "timezone-error" : undefined
                }
                {...register("timezone")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
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
                aria-invalid={!!errors.language}
                aria-describedby={
                  errors.language ? "language-error" : undefined
                }
                {...register("language")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
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
                aria-invalid={!!errors.currency}
                aria-describedby={
                  errors.currency ? "currency-error" : undefined
                }
                {...register("currency")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
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
                aria-invalid={!!errors.date_format}
                aria-describedby={
                  errors.date_format ? "date-format-error" : undefined
                }
                {...register("date_format")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
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

          {/* Branding */}
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* Primary Color */}
            <div className="space-y-2">
              <label
                htmlFor="primary_color"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Primary Color
              </label>

              <Controller
                name="primary_color"
                control={control}
                render={({ field }) => (
                  <div className="flex items-center gap-3">
                    <input
                      id="primary_color"
                      type="color"
                      value={field.value}
                      onChange={field.onChange}
                      className="h-10 w-12 cursor-pointer rounded border border-border bg-background focus:ring-2 focus:ring-primary/20"
                    />

                    <input
                      type="text"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="#2563EB"
                      className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                  </div>
                )}
              />

              <div
                className="mt-2 h-8 rounded-md border border-border"
                style={{
                  backgroundColor: previewPrimary,
                }}
              />

              {errors.primary_color && (
                <p className="text-xs text-destructive">
                  {errors.primary_color.message}
                </p>
              )}
            </div>

            {/* Secondary Color */}
            <div className="space-y-2">
              <label
                htmlFor="secondary_color"
                className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
              >
                Secondary Color
              </label>

              <Controller
                name="secondary_color"
                control={control}
                render={({ field }) => (
                  <div className="flex items-center gap-3">
                    <input
                      id="secondary_color"
                      type="color"
                      value={field.value}
                      onChange={field.onChange}
                      className="h-10 w-12 cursor-pointer rounded border border-border bg-background focus:ring-2 focus:ring-primary/20"
                    />

                    <input
                      type="text"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="#0F172A"
                      className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                  </div>
                )}
              />

              <div
                className="mt-2 h-8 rounded-md border border-border"
                style={{
                  backgroundColor: previewSecondary,
                }}
              />

              {errors.secondary_color && (
                <p className="text-xs text-destructive">
                  {errors.secondary_color.message}
                </p>
              )}
            </div>
          </div>

          {/* Brand Live Preview Card */}
          <div className="rounded-lg border border-border p-5 bg-muted/10 space-y-4">
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-foreground">
                Brand Preview
              </h3>
              <p className="text-xs text-muted-foreground">
                Preview how your workspace branding will appear.
              </p>
            </div>

            <div className="flex items-center gap-4">
              {previewLogo ? (
                <img
                  src={previewLogo ?? undefined}
                  alt="Workspace logo preview"
                  className="h-16 w-16 rounded-lg border border-border object-cover"
                />
              ) : (
                <div
                  className="flex h-16 w-16 items-center justify-center rounded-lg border border-border font-bold text-sm text-primary-foreground shadow-sm"
                  style={{
                    backgroundColor: previewPrimary,
                  }}
                >
                  {getInitials(previewName)}
                </div>
              )}

              <div>
                <h3 className="font-semibold text-sm">
                  {previewName || "Workspace Name"}
                </h3>
                <p className="text-xs text-muted-foreground">
                  Workspace Preview
                </p>
              </div>
            </div>

            <div className="flex gap-3">
              <button
                type="button"
                className="rounded-md px-4 py-2 text-xs font-medium text-white transition hover:opacity-90"
                style={{
                  backgroundColor: previewPrimary,
                }}
              >
                Primary Button
              </button>

              <button
                type="button"
                className="rounded-md px-4 py-2 text-xs font-medium border border-border text-white transition hover:opacity-90"
                style={{
                  backgroundColor: previewSecondary,
                }}
              >
                Secondary Button
              </button>
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
              aria-label="Workspace Active"
              {...register("is_active")}
              className="h-5 w-5"
            />
          </div>

          {/* Action Buttons */}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              aria-label="Reset workspace changes"
              onClick={handleReset}
              disabled={!isDirty}
              className="rounded-lg border border-border bg-background px-5 py-2 text-sm font-medium text-foreground transition hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset
            </button>

            <button
              type="submit"
              aria-label="Save workspace settings"
              aria-busy={isSaving}
              disabled={!isDirty || isSaving}
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
    </div>
  );
};

export default Workspace;
