import React, { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Sliders,
  Plus,
  Trash2,
  Edit2,
  AlertCircle,
  RefreshCw,
  Clock,
  Loader2,
  Copy,
  Play,
} from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { RuleForm } from "@/pages/Automation/RuleForm";
import { RuleTestDialog } from "@/pages/Automation/RuleTestDialog";
import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import { getFriendlyFieldName } from "@/constants/automationFields";
import type { AutomationRule } from "@/types/automation";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

// Centralized Query Cache Keys matching our approved system configurations
const RULES_QUERY_KEY = ["automation-rules"] as const;
const LOGS_QUERY_KEY = ["automation-logs"] as const;

// Human-readable labels for logic operators displayed in lists
const OPERATOR_DISPLAY_MAP: Record<string, string> = {
  EQUALS: "Equals",
  NOT_EQUALS: "Not Equals",
  CONTAINS: "Contains",
  GREATER_THAN: "Greater Than",
  LESS_THAN: "Less Than",
  GREATER_THAN_OR_EQUAL: "Greater Than or Equal",
  LESS_THAN_OR_EQUAL: "Less Than or Equal",
};

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
  const [ruleToDuplicate, setRuleToDuplicate] = useState<AutomationRule | null>(
    null
  );
  const [ruleToTest, setRuleToTest] = useState<AutomationRule | null>(null);
  const [ruleToDelete, setRuleToDelete] = useState<AutomationRule | null>(null);
  const [togglingRuleId, setTogglingRuleId] = useState<string | null>(null);

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

  // 4. Register toggles mutation to modify active states dynamically with Optimistic UI updates
  const { mutate: triggerToggleActive, isPending: isUpdatingRule } =
    useMutation({
      mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
        automationApi.updateAutomationRule(id, { is_active }),

      onMutate: async (updatedRule) => {
        setTogglingRuleId(updatedRule.id);
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

      onError: (err, variables, context) => {
        setTogglingRuleId(null);
        if (context?.previousRules) {
          queryClient.setQueryData(RULES_QUERY_KEY, context.previousRules);
        }

        if (err instanceof ApiError) {
          toast.error(err.message);
        } else {
          toast.error(
            `Failed to ${
              variables.is_active ? "enable" : "disable"
            } automation rule.`
          );
        }
      },

      onSuccess: (_data, variables) => {
        toast.success(
          variables.is_active
            ? "Rule enabled successfully."
            : "Rule disabled successfully."
        );
      },

      onSettled: () => {
        setTogglingRuleId(null);
        queryClient.invalidateQueries({
          queryKey: RULES_QUERY_KEY,
        });
      },
    });

  // --- Click Handler Callbacks (Memoized to preserve React.memo benefits in child elements) ---

  const handleOpenCreateForm = useCallback((): void => {
    setRuleToEdit(null);
    setRuleToDuplicate(null);
    setIsFormOpen(true);
  }, []);

  const handleOpenEditForm = useCallback((rule: AutomationRule): void => {
    setRuleToDuplicate(null);
    setRuleToEdit(rule);
    setIsFormOpen(true);
  }, []);

  const handleOpenDuplicateForm = useCallback((rule: AutomationRule): void => {
    setRuleToEdit(null);
    setRuleToDuplicate(rule);
    setIsFormOpen(true);
  }, []);

  const handleFormClose = useCallback((): void => {
    setIsFormOpen(false);
    setRuleToEdit(null);
    setRuleToDuplicate(null);
  }, []);

  const handleOpenTestDialog = useCallback((rule: AutomationRule): void => {
    setRuleToTest(rule);
  }, []);

  const handleCloseTestDialog = useCallback((): void => {
    setRuleToTest(null);
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
            <div className="text-center py-14 px-6 bg-card border border-dashed border-border/60 rounded-2xl select-none flex flex-col items-center justify-center space-y-3">
              <div className="h-14 w-14 rounded-full bg-primary/10 text-primary flex items-center justify-center mb-1">
                <Sliders className="h-7 w-7" />
              </div>
              <div className="space-y-1 max-w-sm">
                <h4 className="text-base font-extrabold tracking-tight">
                  No automation rules configured
                </h4>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Create your first workflow automation rule to automatically
                  evaluate processed documents and dispatch alerts.
                </p>
              </div>
              <button
                type="button"
                onClick={handleOpenCreateForm}
                disabled={isDeletingRule || isUpdatingRule}
                className="mt-2 inline-flex items-center px-4 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98]"
              >
                <Plus className="h-4 w-4 mr-1.5 flex-shrink-0" />
                Create First Rule
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {rules.map((rule) => (
                <article
                  key={rule.id}
                  className={`p-5 bg-card border rounded-xl shadow-sm transition-all duration-200 hover:shadow-md flex flex-col justify-between space-y-4
                    ${
                      rule.is_active
                        ? "border-border/80 shadow-md ring-1 ring-emerald-500/10"
                        : "border-muted/40 bg-muted/10 opacity-70 filter grayscale-[15%]"
                    }`}
                >
                  {/* Modern Header Row */}
                  <div className="flex justify-between items-start space-x-4">
                    <div className="min-w-0 space-y-2">
                      <h4 className="font-extrabold text-base leading-snug truncate text-foreground">
                        {rule.name}
                      </h4>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-md font-bold select-none whitespace-nowrap">
                          Priority #{rule.priority}
                        </span>
                        <span className="text-[10px] bg-secondary text-secondary-foreground border border-border/40 px-2 py-0.5 rounded-md font-semibold select-none whitespace-nowrap">
                          Event: {rule.event.replace("WORK_ITEM_", "")}
                        </span>
                        {rule.is_active ? (
                          <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-2.5 py-0.5 rounded-full font-bold select-none whitespace-nowrap">
                            Active
                          </span>
                        ) : (
                          <span className="text-[10px] bg-muted text-muted-foreground px-2.5 py-0.5 rounded-full font-bold select-none whitespace-nowrap">
                            Disabled
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Active toggle switch control */}
                    <button
                      type="button"
                      role="switch"
                      aria-checked={rule.is_active}
                      onClick={() =>
                        triggerToggleActive({
                          id: rule.id,
                          is_active: !rule.is_active,
                        })
                      }
                      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary/20 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50
                        ${
                          rule.is_active
                            ? "bg-emerald-500"
                            : "bg-muted-foreground/30"
                        }`}
                      disabled={
                        togglingRuleId === rule.id ||
                        isDeletingRule ||
                        isUpdatingRule
                      }
                      title={
                        rule.is_active ? "Deactivate rule" : "Activate rule"
                      }
                      aria-label={`Toggle rule ${rule.name}`}
                    >
                      <span className="sr-only">Toggle rule status</span>
                      <span
                        className={`pointer-events-none relative inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition duration-200 ease-in-out flex items-center justify-center
                          ${
                            rule.is_active ? "translate-x-5" : "translate-x-0"
                          }`}
                      >
                        {togglingRuleId === rule.id && (
                          <Loader2 className="h-3 w-3 animate-spin text-primary" />
                        )}
                      </span>
                    </button>
                  </div>

                  {/* Structured IF / THEN Panel supporting multiple conditions and multiple actions */}
                  <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 bg-muted/30 dark:bg-muted/10 border border-border/40 rounded-xl p-3.5 select-none">
                    {/* IF Section */}
                    <div className="sm:col-span-7 flex flex-col justify-center space-y-2.5 border-b sm:border-b-0 pb-2.5 sm:pb-0">
                      <span className="text-[10px] font-black uppercase tracking-wider text-primary">
                        IF Conditions ({rule.logic_operator})
                      </span>
                      <div className="flex flex-col gap-2">
                        {rule.conditions.map((cond, idx) => (
                          <React.Fragment key={idx}>
                            {idx > 0 && (
                              <div className="flex items-center space-x-2 select-none">
                                <span className="text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-black uppercase">
                                  {rule.logic_operator}
                                </span>
                                <div className="h-px bg-border/40 flex-1" />
                              </div>
                            )}
                            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                              <span className="px-2 py-1 rounded-md bg-background border border-border/60 font-mono font-bold text-foreground truncate max-w-[150px]">
                                {getFriendlyFieldName(cond.field)}
                              </span>
                              <span className="text-[10px] uppercase font-bold text-muted-foreground px-1">
                                {OPERATOR_DISPLAY_MAP[cond.operator] ??
                                  cond.operator.replace("_", " ")}
                              </span>
                              <span className="px-2 py-1 rounded-md bg-primary/10 text-primary border border-primary/20 font-bold truncate max-w-[150px]">
                                {cond.value}
                              </span>
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                    </div>

                    {/* THEN Section supporting multiple actions */}
                    <div className="sm:col-span-5 flex flex-col justify-center sm:border-l border-border/40 pt-1.5 sm:pt-0 sm:pl-3.5 space-y-2.5">
                      <span className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">
                        THEN Actions ({rule.actions.length})
                      </span>
                      <div className="flex flex-col gap-2 select-none">
                        {rule.actions.map((act, idx) => (
                          <div
                            key={idx}
                            className="flex items-center space-x-2 text-xs font-bold text-foreground"
                          >
                            <span className="text-[9px] text-muted-foreground bg-muted border border-border/40 px-1.5 py-0.5 rounded font-mono">
                              #{idx + 1}
                            </span>
                            <span
                              className="px-2 py-1 rounded-md bg-background border border-border/60 text-emerald-600 dark:text-emerald-400 truncate max-w-[150px]"
                              title={act.config?.recipient as string}
                            >
                              {act.action_type.replace("_", " ")}

                              {"recipient" in act.config &&
                                typeof act.config.recipient === "string" && (
                                  <> ({act.config.recipient})</>
                                )}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Metadata Row & Action Toolbar */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2 border-t border-border/10">
                    <div className="text-[10px] text-muted-foreground font-medium flex flex-wrap items-center gap-x-3 gap-y-1 select-none">
                      <span>Created: {formatDateTime(rule.created_at)}</span>
                      {rule.updated_at && (
                        <span>Updated: {formatDateTime(rule.updated_at)}</span>
                      )}
                    </div>

                    {/* Action Buttons Toolbar */}
                    <div className="flex items-center space-x-1 self-end sm:self-auto">
                      <button
                        type="button"
                        disabled={isDeletingRule || isUpdatingRule}
                        onClick={() => handleOpenTestDialog(rule)}
                        className="p-2 rounded-lg bg-background border border-border/40 hover:bg-muted/80 hover:border-border text-muted-foreground hover:text-foreground transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Test automation rule"
                        aria-label={`Test rule ${rule.name}`}
                      >
                        <Play className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        disabled={isDeletingRule || isUpdatingRule}
                        onClick={() => handleOpenDuplicateForm(rule)}
                        className="p-2 rounded-lg bg-background border border-border/40 hover:bg-muted/80 hover:border-border text-muted-foreground hover:text-foreground transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Duplicate automation rule"
                        aria-label={`Duplicate rule ${rule.name}`}
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        disabled={isDeletingRule || isUpdatingRule}
                        onClick={() => handleOpenEditForm(rule)}
                        className="p-2 rounded-lg bg-background border border-border/40 hover:bg-muted/80 hover:border-border text-muted-foreground hover:text-foreground transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Edit rule configurations"
                        aria-label={`Edit rule ${rule.name}`}
                      >
                        <Edit2 className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setRuleToDelete(rule);
                        }}
                        disabled={isDeletingRule || isUpdatingRule}
                        className="p-2 rounded-lg bg-background border border-border/40 hover:bg-destructive/10 hover:border-destructive/30 hover:text-destructive text-muted-foreground transition-all focus:outline-none focus:ring-2 focus:ring-destructive/20 disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Delete automation rule"
                        aria-label={`Delete rule ${rule.name}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
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
        ruleToDuplicate={ruleToDuplicate}
        existingRules={rules}
      />
      <RuleTestDialog
        isOpen={ruleToTest !== null}
        onClose={handleCloseTestDialog}
        rule={ruleToTest}
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
