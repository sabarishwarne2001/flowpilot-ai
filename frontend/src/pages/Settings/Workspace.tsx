import React, { useEffect } from "react";

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { getWorkspace, saveWorkspace } from "@/services/api/workspace";

import { ApiError } from "@/services/api/client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";

export const Workspace: React.FC = () => {
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

      primary_color: "#2563EB",
      secondary_color: "#0F172A",

      is_active: true,
    },
  });

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

  const {
    mutateAsync: saveWorkspaceMutation,
    isPending: isSaving,
  } = useMutation({
    mutationFn: saveWorkspace,

    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["workspace"],
      });

      toast.success("Workspace settings saved successfully.");
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message);
        return;
      }

      toast.error("Failed to save workspace settings.");
    },
  });

  const onSubmit = async (
    data: WorkspaceFormData,
  ): Promise<void> => {
    await saveWorkspaceMutation({
      ...data,
      company_logo_url:
        !data.company_logo_url?.trim()
          ? null
          : data.company_logo_url,
    });
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
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.workspace_name && (
              <p className="text-xs text-destructive">
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
              placeholder="FlowPilot AI"
              {...register("company_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.company_name && (
              <p className="text-xs text-destructive">
                {errors.company_name.message}
              </p>
            )}
          </div>

          {/* Company Logo URL */}
          <div className="space-y-2">
            <label
              htmlFor="company_logo_url"
              className="text-xs font-bold uppercase tracking-wide text-muted-foreground"
            >
              Company Logo URL
            </label>

            <input
              id="company_logo_url"
              type="url"
              placeholder="https://example.com/logo.png"
              {...register("company_logo_url")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />

            {errors.company_logo_url && (
              <p className="text-xs text-destructive">
                {errors.company_logo_url.message}
              </p>
            )}
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
                {...register("timezone")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
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

              {errors.timezone && (
                <p className="text-xs text-destructive">
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
                <p className="text-xs text-destructive">
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
                <p className="text-xs text-destructive">
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
                <p className="text-xs text-destructive">
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

              <input
                id="primary_color"
                type="color"
                {...register("primary_color")}
                className="h-12 w-full rounded-lg border border-border bg-background p-2"
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

              <input
                id="secondary_color"
                type="color"
                {...register("secondary_color")}
                className="h-12 w-full rounded-lg border border-border bg-background p-2"
              />

              {errors.secondary_color && (
                <p className="text-xs text-destructive">
                  {errors.secondary_color.message}
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
              type="checkbox"
              {...register("is_active")}
              className="h-5 w-5"
            />
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
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
