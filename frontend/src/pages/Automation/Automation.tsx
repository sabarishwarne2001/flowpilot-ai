import React, { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Sliders,
  Plus,
  Trash2,
  Edit2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
  Clock,
  ArrowRight,
} from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { RuleForm } from "@/pages/Automation/RuleForm";
import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import type { AutomationRule } from "@/types/automation";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

// Centralized Query Cache Keys matching our approved system configurations
const RULES_QUERY_KEY = ["automation-rules"] as const;
const LOGS_QUERY_KEY = ["automation-logs"] as const;

/**
 * Split-pane business Automation Rules and Audit Logs control panel for FlowPilot AI.
 *
 * Supports rule creations and inline modifications, manages real-time toggles
 * of active/inactive states, and lists execution status logs cleanly with trace details.
 */
export const Automation: React.FC = () => {
  const queryClient = useQueryClient();

  // Dialog overlay controller states
  const [isFormOpen, setIsFormOpen] = useState<boolean>(false);
  const [ruleToEdit, setRuleToEdit] = useState<AutomationRule | null>(null);
  const [ruleToDelete, setRuleToDelete] = useState<AutomationRule | null>(null);

  // 1. Query user-configured Rules lists
  const {
    data: rules = [],
    isLoading: isRulesLoading,
    error: rulesError,
  } = useQuery({
    queryKey: RULES_QUERY_KEY,
    queryFn: automationApi.getAutomationRules,
    staleTime: 1000 * 30,
    placeholderData: (previousData) => previousData,
  });

  // 2. Query separate execution logs histories
  const {
    data: logs = [],
    isLoading: isLogsLoading,
    error: logsError,
  } = useQuery({
    queryKey: LOGS_QUERY_KEY,
    queryFn: automationApi.getAutomationLogs,

    staleTime: 5000,

    refetchInterval: 5000,

    refetchOnWindowFocus: true,

    refetchOnReconnect: true,

    placeholderData: (previousData) => previousData,
  });

  // 3. Register rule deletion mutation
  const { mutate: triggerDelete, isPending: isDeletingRule } = useMutation({
    mutationFn: automationApi.deleteAutomationRule,
    onSuccess: async () => {
      toast.success("Automation rule removed from PostgreSQL.");
      // Deletions cascade inside database; re-fetch both lists
      await queryClient.invalidateQueries({ queryKey: RULES_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: LOGS_QUERY_KEY });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        toast.error(err.message || "Failed to remove automation rule.");
      } else {
        toast.error("An unexpected validation failure occurred.");
      }
    },
  });

  // 4. Register toggles mutation to modify active states dynamically
  const { mutate: triggerToggleActive, isPending: isUpdatingRule } =
    useMutation({
      mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
        automationApi.updateAutomationRule(id, { is_active }),

      onMutate: async (updatedRule) => {
        await queryClient.cancelQueries({
          queryKey: RULES_QUERY_KEY,
        });

        const previousRules =
          queryClient.getQueryData<AutomationRule[]>(RULES_QUERY_KEY);

        queryClient.setQueryData<AutomationRule[]>(
          RULES_QUERY_KEY,
          (old = []) =>
            old.map((rule) =>
              rule.id === updatedRule.id
                ? {
                    ...rule,
                    is_active: updatedRule.is_active,
                  }
                : rule
            )
        );

        return { previousRules };
      },

      onError: (err, _, context) => {
        if (context?.previousRules) {
          queryClient.setQueryData(RULES_QUERY_KEY, context.previousRules);
        }

        if (err instanceof ApiError) {
          toast.error(err.message);
        } else {
          toast.error("Failed to update rule.");
        }
      },

      onSuccess: () => {
        toast.success("Rule status updated.");
      },

      onSettled: () => {
        queryClient.invalidateQueries({
          queryKey: RULES_QUERY_KEY,
        });
      },
    });

  // --- Click Handler Callbacks (Memoized to preserve React.memo benefits in child elements) ---

  const handleOpenCreateForm = useCallback((): void => {
    setRuleToEdit(null);
    setIsFormOpen(true);
  }, []);

  const handleOpenEditForm = useCallback((rule: AutomationRule): void => {
    setRuleToEdit(rule);
    setIsFormOpen(true);
  }, []);

  const handleFormClose = useCallback((): void => {
    setIsFormOpen(false);
    setRuleToEdit(null);
  }, []);

  const handleSaveSuccessCallback = useCallback((): void => {
    queryClient.invalidateQueries({ queryKey: RULES_QUERY_KEY });
  }, [queryClient]);

  // Render full-page loaders on initial data syncs
  const isInitializing = isRulesLoading || isLogsLoading;
  if (isInitializing && rules.length === 0 && logs.length === 0) {
    return (
      <div className="space-y-6">
        <header className="flex justify-between items-center select-none">
          <div className="space-y-1">
            <div className="h-6 bg-muted/60 rounded w-48 animate-pulse" />
          </div>
          <div className="h-9 bg-muted/40 rounded w-28 animate-pulse" />
        </header>
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-5">
            <SkeletonCard />
          </div>
          <div className="lg:col-span-7">
            <SkeletonCard />
          </div>
        </section>
      </div>
    );
  }

  // Gracefully present connection retries on errors
  if (rulesError || logsError) {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center text-center p-6 bg-card border border-border/40 rounded-xl max-w-xl mx-auto shadow-sm select-none">
        <div className="h-12 w-12 rounded-full bg-destructive/10 text-destructive flex items-center justify-center mb-4">
          <AlertCircle className="h-6 w-6" />
        </div>
        <h2 className="text-lg font-bold tracking-tight mb-2">
          Failed to load rules
        </h2>
        <p className="text-sm text-muted-foreground font-medium leading-relaxed mb-6">
          There was an error communicating with your automation and log
          databases.
        </p>
        <button
          onClick={async () => {
            await Promise.all([
              queryClient.invalidateQueries({
                queryKey: RULES_QUERY_KEY,
              }),
              queryClient.invalidateQueries({
                queryKey: LOGS_QUERY_KEY,
              }),
            ]);
          }}
          className="flex items-center px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm"
        >
          <RefreshCw className="h-3.5 w-3.5 mr-2" />
          Retry Sync
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* --- Section 1: Top Dashboard Title Header --- */}
      <header className="flex flex-col sm:flex-row justify-between items-start sm:items-center space-y-3 sm:space-y-0 select-none">
        <div className="space-y-1">
          <h2 className="text-2xl font-extrabold tracking-tight">
            Business Workflows
          </h2>
          <p className="text-sm text-muted-foreground font-semibold leading-relaxed">
            Construct trigger-action automation rules and inspect audit
            execution files in real-time.
          </p>
        </div>
        <button
          type="button"
          onClick={handleOpenCreateForm}
          disabled={isDeletingRule || isUpdatingRule}
          className="flex items-center px-4 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Plus className="h-4 w-4 mr-1.5 flex-shrink-0" />
          Create New Rule
        </button>
      </header>

      {/* --- Section 2: Split Columns Operational Grid --- */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Panel 2A: Rules Management Grid list (Left Column) */}
        <div className="lg:col-span-5 space-y-4">
          <h3 className="text-xs font-black uppercase tracking-wider text-muted-foreground pl-1 select-none">
            Configured Rules ({rules.length})
          </h3>

          {rules.length === 0 ? (
            <div className="text-center py-12 px-6 bg-card border border-border/40 rounded-xl select-none">
              <Sliders className="h-8 w-8 text-muted-foreground mx-auto mb-2 opacity-35" />
              <p className="text-sm font-bold tracking-tight">
                No automation rules configured
              </p>
              <p className="text-xs text-muted-foreground font-semibold mt-1">
                Configure your first rule to send emails oncompleted files.
              </p>
            </div>
          ) : (
            <div className="space-y-3.5">
              {rules.map((rule) => (
                <article
                  key={rule.id}
                  className={`p-5 bg-card border rounded-xl shadow-sm transition-all duration-200 flex flex-col justify-between space-y-4
                    ${
                      rule.is_active
                        ? "border-border/80"
                        : "border-border/40 bg-muted/10 opacity-70"
                    }`}
                >
                  <div className="flex justify-between items-start space-x-4">
                    <div className="min-w-0">
                      <h4 className="font-extrabold text-sm truncate leading-snug">
                        {rule.name}
                      </h4>
                      <span className="text-[10px] text-muted-foreground font-semibold leading-none mt-1.5 block select-none">
                        Event: {rule.event.replace("WORK_ITEM_", "")}
                      </span>
                    </div>

                    {/* Active toggle button control */}
                    <button
                      type="button"
                      onClick={() =>
                        triggerToggleActive({
                          id: rule.id,
                          is_active: !rule.is_active,
                        })
                      }
                      className="text-muted-foreground hover:text-foreground transition-colors focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
                      title={
                        rule.is_active ? "Deactivate rule" : "Activate rule"
                      }
                      disabled={isDeletingRule || isUpdatingRule}
                    >
                      {rule.is_active ? (
                        <ToggleRight className="h-7 w-7 text-emerald-500 fill-emerald-500" />
                      ) : (
                        <ToggleLeft className="h-7 w-7 text-muted-foreground/60" />
                      )}
                    </button>
                  </div>

                  {/* Summary Condition metrics display */}
                  <div className="p-3 bg-muted/40 dark:bg-muted/10 border border-border/20 rounded-lg text-xs font-semibold leading-relaxed flex items-center select-none text-muted-foreground">
                    <div className="truncate">
                      <span className="font-mono text-foreground font-extrabold text-[11px]">
                        {rule.field}
                      </span>
                      <span className="mx-1.5 text-primary text-[10px] uppercase font-bold">
                        {rule.operator.replace("_", " ")}
                      </span>
                      <span className="font-bold text-foreground bg-background px-1.5 py-0.5 rounded border border-border/50">
                        {rule.value}
                      </span>
                    </div>
                    <ArrowRight className="h-3 w-3 mx-2 text-muted-foreground/60 flex-shrink-0" />
                    <span className="text-primary font-bold">
                      {rule.action_type}
                    </span>
                  </div>

                  {/* Edit / Delete control buttons */}
                  <div className="flex items-center justify-end space-x-2 pt-2 border-t border-border/10">
                    <button
                      type="button"
                      disabled={isDeletingRule || isUpdatingRule}
                      onClick={() => handleOpenEditForm(rule)}
                      className="p-1.5 rounded bg-muted/60 dark:bg-muted/5 border border-border/40 hover:bg-muted text-muted-foreground hover:text-foreground transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Edit rule configurations"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setRuleToDelete(rule);
                      }}
                      disabled={isDeletingRule || isUpdatingRule}
                      className="p-1.5 rounded bg-muted/60 dark:bg-muted/5 border border-border/40 hover:bg-destructive/10 hover:text-destructive text-muted-foreground transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Delete automation rule"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        {/* Panel 2B: Historical Rules Execution Audit Logs (Right Column) */}
        <div className="lg:col-span-7 bg-card border border-border/60 dark:border-border/40 rounded-xl overflow-hidden shadow-sm flex flex-col h-[550px]">
          <header className="p-5 border-b border-border/40 bg-muted/5 select-none">
            <h3 className="text-sm font-extrabold tracking-tight">
              Execution Audit Log history
            </h3>
            <p className="text-xs text-muted-foreground font-bold mt-1">
              Symmetrical run logs tracking trigger executions states.
            </p>
          </header>

          {/* List timelines scroller */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar bg-muted/5">
            {logs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center p-8 select-none text-muted-foreground">
                <Clock className="h-8 w-8 mb-2 opacity-35 animate-pulse" />
                <p className="text-xs font-semibold">
                  No workflow execution logs recorded in this environment.
                </p>
              </div>
            ) : (
              logs.map((log) => (
                <div
                  key={log.id}
                  className="rounded-xl border border-border/40 bg-background p-4 shadow-sm transition-all hover:border-border/80"
                >
                  {/* Header */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h4 className="truncate text-sm font-bold text-foreground">
                        {log.rule_name}
                      </h4>

                      <p className="mt-1 text-xs text-muted-foreground">
                        {log.document_name}
                      </p>
                    </div>

                    <span className="text-[10px] font-semibold text-muted-foreground whitespace-nowrap">
                      {formatDateTime(log.created_at)}
                    </span>
                  </div>

                  {/* Action + Status */}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <span className="rounded-md bg-primary/10 px-2 py-1 text-[10px] font-bold text-primary">
                      {log.action_type}
                    </span>

                    <span
                      className={`rounded-md px-2 py-1 text-[10px] font-bold ${
                        log.status === "SUCCESS"
                          ? "bg-emerald-500/10 text-emerald-500"
                          : "bg-destructive/10 text-destructive"
                      }`}
                    >
                      {log.status}
                    </span>
                  </div>

                  {/* Log Message */}
                  {log.log_message && (
                    <div className="mt-3">
                      <pre
                        className={`overflow-x-auto rounded-lg border p-3 text-[11px] font-mono leading-relaxed ${
                          log.status === "FAILED"
                            ? "border-destructive/20 bg-destructive/5 text-destructive"
                            : "border-border/40 bg-muted/30 text-muted-foreground"
                        }`}
                      >
                        {log.log_message}
                      </pre>
                    </div>
                  )}

                  {/* Copy Error */}
                  {log.status === "FAILED" && log.log_message && (
                    <div className="mt-3 flex justify-end">
                      <button
                        type="button"
                        onClick={() => {
                          if (!log.log_message) return;

                          navigator.clipboard.writeText(log.log_message);

                          toast.success("Error copied.");
                        }}
                        className="rounded-md border border-border px-3 py-1 text-xs hover:bg-muted"
                      >
                        Copy Error
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      {/* --- Section 3: Controlled Rules Creation modal --- */}
      <RuleForm
        isOpen={isFormOpen}
        onClose={handleFormClose}
        onSaveSuccess={handleSaveSuccessCallback}
        ruleToEdit={ruleToEdit}
      />
      <ConfirmDialog
        open={ruleToDelete !== null}
        title="Delete Automation Rule"
        message={
          ruleToDelete
            ? `Are you sure you want to delete "${ruleToDelete.name}"? This action cannot be undone.`
            : ""
        }
        confirmText="Delete Rule"
        cancelText="Cancel"
        loading={isDeletingRule}
        onCancel={() => setRuleToDelete(null)}
        onConfirm={() => {
          if (!ruleToDelete) return;

          triggerDelete(ruleToDelete.id);

          setRuleToDelete(null);
        }}
      />
    </div>
  );
};

export default Automation;
