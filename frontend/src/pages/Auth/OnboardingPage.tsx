import React from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Check, X, Mail } from "lucide-react";

import { saveWorkspace, listMyInvitations, acceptInvitation, rejectInvitation } from "@/services/api/workspace";
import { workspaceSchema, type WorkspaceFormData } from "@/schemas/workspace";
import { ROUTES } from "@/constants/routes";

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

  // Query received invitations
  const { data: invitations = [], isLoading: isLoadingInvites } = useQuery({
    queryKey: ["my_received_invitations"],
    queryFn: listMyInvitations,
  });

  const { mutateAsync: onboardMutation, isPending } = useMutation({
    mutationFn: saveWorkspace,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["workspace"] });
      toast.success("Workspace initialized successfully!");
      navigate(ROUTES.DASHBOARD);
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to initialize workspace.");
    },
  });

  const { mutateAsync: acceptMutation } = useMutation({
    mutationFn: acceptInvitation,
    onSuccess: async () => {
      toast.success("Successfully joined the workspace!");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspace"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace_members"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace_membership_me"] }),
        queryClient.invalidateQueries({ queryKey: ["my_received_invitations"] }),
      ]);
      navigate(ROUTES.DASHBOARD);
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to accept the invitation.");
    },
  });

  const { mutateAsync: rejectMutation } = useMutation({
    mutationFn: rejectInvitation,
    onSuccess: async () => {
      toast.success("Invitation declined.");
      await queryClient.invalidateQueries({ queryKey: ["my_received_invitations"] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to decline the invitation.");
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
      <div className="max-w-md w-full space-y-6">

        {/* Pending Received Invitations Portal */}
        {!isLoadingInvites && invitations.length > 0 && (
          <div className="bg-card border-2 border-primary/20 p-6 rounded-xl shadow-lg space-y-4 animate-scale-in">
            <div className="flex items-center space-x-2 text-primary">
              <Mail className="h-5 w-5" />
              <h2 className="text-sm font-extrabold uppercase tracking-wider">Workspace Invitations</h2>
            </div>
            <p className="text-xs text-muted-foreground leading-normal font-semibold">
              You have outstanding pending invitations. Accept to join immediately:
            </p>
            <div className="space-y-3">
              {invitations.map((inv) => (
                <div key={inv.id} className="p-3.5 bg-muted/20 border border-border/60 rounded-lg flex items-center justify-between gap-3 text-xs">
                  <div className="min-w-0 space-y-0.5">
                    <p className="font-extrabold text-foreground truncate">
                      Role: {inv.role}
                    </p>
                    <p className="text-muted-foreground text-[10px]">
                      Created: {new Date(inv.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex items-center space-x-1.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => acceptMutation(inv.token)}
                      className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 transition-all"
                      title="Accept Invitation"
                    >
                      <Check className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => rejectMutation(inv.token)}
                      className="p-1.5 rounded-lg bg-destructive/10 text-destructive hover:bg-destructive/20 transition-all"
                      title="Decline Invitation"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Workspace Creation Wizard */}
        <div className="bg-card border border-border p-8 rounded-xl shadow-lg space-y-6">
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
    </div>
  );
};

export default OnboardingPage;
