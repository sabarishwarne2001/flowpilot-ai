import React from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { saveWorkspace } from "@/services/api/workspace";
import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";

export const OnboardingPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<WorkspaceFormData>({
    resolver: zodResolver(workspaceSchema),
    defaultValues: {
      workspace_name: "",
      company_name: "",
      timezone: "UTC",
      language: "en",
      currency: "USD",
      date_format: "YYYY-MM-DD",
      is_active: true,
    },
  });

  const { mutateAsync: onboardMutation, isPending } = useMutation({
    mutationFn: saveWorkspace,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["workspace"] });
      toast.success("Workspace initialized successfully!");
      navigate("/");
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to initialize workspace.");
    },
  });

  const onSubmit = async (data: WorkspaceFormData) => {
    await onboardMutation({
      ...data,
      company_logo_url: null,
    });
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center text-foreground p-4">
      <div className="bg-card border border-border p-8 rounded-xl shadow-lg max-w-md w-full space-y-6">
        <div className="space-y-2 text-center">
          <h1 className="text-2xl font-bold">Welcome to FlowPilot AI!</h1>
          <p className="text-sm text-muted-foreground font-semibold">
            Get started by initializing your organization's primary workspace.
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-1">
            <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Workspace Name
            </label>
            <input
              type="text"
              placeholder="E.g., Engineering Team"
              {...register("workspace_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />
            {errors.workspace_name && (
              <p className="text-xs text-destructive">{errors.workspace_name.message}</p>
            )}
          </div>

          <div className="space-y-1">
            <label className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Company Name
            </label>
            <input
              type="text"
              placeholder="E.g., FlowPilot Inc."
              {...register("company_name")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />
            {errors.company_name && (
              <p className="text-xs text-destructive">{errors.company_name.message}</p>
            )}
          </div>

          <button
            type="submit"
            disabled={isPending}
            className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            {isPending ? "Initializing..." : "Create My Workspace"}
          </button>
        </form>
      </div>
    </div>
  );
};

export default OnboardingPage;
