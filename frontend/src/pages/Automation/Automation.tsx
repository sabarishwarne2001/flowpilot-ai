import React, { useState, useCallback, useMemo } from "react";
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
  CheckCircle2,
  XCircle,
  Search,
  Activity,
  AlertTriangle,
  PieChart,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { RuleForm } from "@/pages/Automation/RuleForm";
import { RuleTestDialog } from "@/pages/Automation/RuleTestDialog";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import { getFriendlyFieldName } from "@/constants/automationFields";
import type { AutomationRule } from "@/types/automation";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/Select";

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

  // Search & Filters Panel states (Rule list)
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<
    "ALL" | "ENABLED" | "DISABLED"
  >("ALL");
  const [eventFilter, setEventFilter] = useState<string>("ALL");
  const [sortBy, setSortBy] = useState<string>("PRIORITY_ASC");

  // Advanced Audit Log Filter states (Log list)
  const [logSearchQuery, setLogSearchQuery] = useState<string>("");
  const [logStatusFilter, setLogStatusFilter] = useState<
    "ALL" | "SUCCESS" | "FAILED"
  >("ALL");
  const [logRuleFilter, setLogRuleFilter] = useState<string>("ALL");
  const [logDateRangeFilter, setLogDateRangeFilter] = useState<
    "ALL" | "TODAY" | "7_DAYS" | "30_DAYS"
  >("ALL");
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

  // 1. Query user-configured Rules lists
  const {
    data: rules = [],
    isLoading: isRulesLoading,
    error: rulesError,
    refetch: refetchRules,
    isRefetching: isRulesRefetching,
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
    refetch: refetchLogs,
    isRefetching: isLogsRefetching,
  } = useQuery({
    queryKey: LOGS_QUERY_KEY,
    queryFn: automationApi.getAutomationLogs,
    staleTime: 5000,
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    placeholderData: (previousData) => previousData,
  });

  // Sync refresh utility
  const handleManualSync = useCallback(async () => {
    await Promise.all([refetchRules(), refetchLogs()]);
    toast.success("Dashboard metrics synced successfully.");
  }, [refetchRules, refetchLogs]);

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

  // --- KPI Statistics & Insights Calculations ---
  const stats = useMemo(() => {
    const total = rules.length;
    const active = rules.filter((r) => r.is_active).length;
    const disabled = total - active;
    const activePct = total > 0 ? Math.round((active / total) * 100) : 0;
    const disabledPct = total > 0 ? 100 - activePct : 0;
    const avgPriority =
      total > 0
        ? Math.round(rules.reduce((acc, r) => acc + r.priority, 0) / total)
        : 0;

    const sortedByPriority = [...rules].sort((a, b) => a.priority - b.priority);
    const highestPriority = sortedByPriority[0] || null;

    const successLogsCount = logs.filter((l) => l.status === "SUCCESS").length;
    const failedLogsCount = logs.filter((l) => l.status === "FAILED").length;
    const totalLogs = successLogsCount + failedLogsCount;

    // Explicitly fallback Success/Failure rates to 0% when no execution logs exist
    const successRate =
      totalLogs > 0 ? Math.round((successLogsCount / totalLogs) * 100) : 0;
    const failureRate = totalLogs > 0 ? 100 - successRate : 0;

    return {
      total,
      active,
      disabled,
      activePct,
      disabledPct,
      avgPriority,
      highestPriority,
      successRate,
      failureRate,
    };
  }, [rules, logs]);

  // --- Search, Filtering & Sorting logic (Rule list) ---
  const filteredRules = useMemo(() => {
    return rules.filter((rule) => {
      const matchesSearch = rule.name
        .toLowerCase()
        .includes(searchQuery.toLowerCase());
      const matchesStatus =
        statusFilter === "ALL"
          ? true
          : statusFilter === "ENABLED"
          ? rule.is_active
          : !rule.is_active;
      const matchesEvent =
        eventFilter === "ALL" ? true : rule.event === eventFilter;
      return matchesSearch && matchesStatus && matchesEvent;
    });
  }, [rules, searchQuery, statusFilter, eventFilter]);

  const sortedRules = useMemo(() => {
    const sorted = [...filteredRules];
    if (sortBy === "NAME_ASC") {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    } else if (sortBy === "NAME_DESC") {
      sorted.sort((a, b) => b.name.localeCompare(a.name));
    } else if (sortBy === "PRIORITY_ASC") {
      sorted.sort((a, b) => a.priority - b.priority);
    } else if (sortBy === "PRIORITY_DESC") {
      sorted.sort((a, b) => b.priority - a.priority);
    } else if (sortBy === "CREATED_DESC") {
      sorted.sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
    } else if (sortBy === "UPDATED_DESC") {
      sorted.sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
    }
    return sorted;
  }, [filteredRules, sortBy]);

  // --- Search, Filtering & Statistics logic (Advanced Audit Logs) ---
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      // Search matches rule name or document name
      const matchesSearch =
        log.rule_name.toLowerCase().includes(logSearchQuery.toLowerCase()) ||
        log.document_name.toLowerCase().includes(logSearchQuery.toLowerCase());

      // Status filters
      const matchesStatus =
        logStatusFilter === "ALL" ? true : log.status === logStatusFilter;

      // Associated Rule filter
      const matchesRule =
        logRuleFilter === "ALL" ? true : log.rule_id === logRuleFilter;

      // Date Range filters
      let matchesDate = true;
      if (logDateRangeFilter !== "ALL") {
        const logTime = new Date(log.created_at).getTime();
        const now = Date.now();
        if (logDateRangeFilter === "TODAY") {
          const startOfToday = new Date().setHours(0, 0, 0, 0);
          matchesDate = logTime >= startOfToday;
        } else if (logDateRangeFilter === "7_DAYS") {
          matchesDate = now - logTime <= 7 * 24 * 60 * 60 * 1000;
        } else if (logDateRangeFilter === "30_DAYS") {
          matchesDate = now - logTime <= 30 * 24 * 60 * 60 * 1000;
        }
      }

      return matchesSearch && matchesStatus && matchesRule && matchesDate;
    });
  }, [
    logs,
    logSearchQuery,
    logStatusFilter,
    logRuleFilter,
    logDateRangeFilter,
  ]);

  const auditStats = useMemo(() => {
    const total = filteredLogs.length;
    const successful = filteredLogs.filter(
      (l) => l.status === "SUCCESS"
    ).length;
    const failed = total - successful;
    const successPct = total > 0 ? Math.round((successful / total) * 100) : 0;
    const failurePct = total > 0 ? 100 - successPct : 0;

    return {
      total,
      successful,
      failed,
      successPct,
      failurePct,
    };
  }, [filteredLogs]);

  // --- Click Handler Callbacks (Memoized) ---
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

  // Render Skeleton Cards while querying initially
  const isInitializing = isRulesLoading || isLogsLoading;
  if (isInitializing && rules.length === 0 && logs.length === 0) {
    return (
      <div className="space-y-6">
        <header className="flex justify-between items-center select-none animate-pulse">
          <div className="h-8 bg-muted/60 rounded w-48" />
          <div className="h-9 bg-muted/40 rounded w-28" />
        </header>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-4 animate-pulse">
          {Array.from({ length: 6 }).map((_, idx) => (
            <div
              key={idx}
              className="h-24 bg-muted/40 border border-border/40 rounded-xl"
            />
          ))}
        </div>
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-pulse">
          <div className="lg:col-span-5 space-y-4">
            <div className="h-6 bg-muted/60 rounded w-32" />
            {Array.from({ length: 2 }).map((_, idx) => (
              <div
                key={idx}
                className="h-44 bg-muted/30 border border-border/40 rounded-xl"
              />
            ))}
          </div>
          <div className="lg:col-span-7 h-[500px] bg-muted/20 border border-border/40 rounded-xl" />
        </section>
      </div>
    );
  }

  // Gracefully present connection retries on errors
  if (rulesError || logsError) {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center text-center p-6 bg-card border border-border/40 rounded-xl max-w-xl mx-auto shadow-sm select-none">
        <div className="h-12 w-12 rounded-full bg-destructive/10 text-destructive flex items-center justify-center mb-4 animate-bounce">
          <AlertCircle className="h-6 w-6" />
        </div>
        <h2 className="text-lg font-bold tracking-tight mb-2">
          Failed to load rules metrics
        </h2>
        <p className="text-sm text-muted-foreground font-medium leading-relaxed mb-6">
          There was an error communicating with your automation and log
          databases.
        </p>
        <button
          onClick={handleManualSync}
          className="flex items-center px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm"
        >
          <RefreshCw className="h-3.5 w-3.5 mr-2" />
          Retry Sync
        </button>
      </div>
    );
  }

  const isSyncPending = isRulesRefetching || isLogsRefetching;

  return (
    <div className="space-y-6">
      {/* --- Section 1: Top Dashboard Title Header --- */}
      <header className="flex flex-col sm:flex-row justify-between items-start sm:items-center space-y-3 sm:space-y-0 select-none">
        <div className="space-y-1">
          <h2 className="text-2xl font-extrabold tracking-tight">
            Automation Dashboard
          </h2>
          <p className="text-xs text-muted-foreground font-semibold leading-relaxed">
            Construct trigger-action automation rules and inspect audit
            execution files in real-time.
          </p>
        </div>
        <div className="flex items-center space-x-2 w-full sm:w-auto">
          <button
            type="button"
            onClick={handleManualSync}
            disabled={isSyncPending}
            className="flex items-center justify-center p-2 bg-background border border-border hover:bg-muted text-muted-foreground rounded-lg transition-all"
            title="Sync metrics manually"
          >
            <RefreshCw
              className={`h-4 w-4 ${
                isSyncPending ? "animate-spin text-primary" : ""
              }`}
            />
          </button>
          <button
            type="button"
            onClick={handleOpenCreateForm}
            disabled={isDeletingRule || isUpdatingRule}
            className="flex-1 sm:flex-initial flex items-center justify-center px-4 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] disabled:opacity-50"
          >
            <Plus className="h-4 w-4 mr-1.5 flex-shrink-0" />
            Create New Rule
          </button>
        </div>
      </header>

      {/* --- Section 2: Summary metrics (KPI Cards Grid) --- */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-4 select-none">
        {/* Total Rules */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              Total Rules
            </span>
            <Sliders className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              {stats.total}
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Configured workspace rules
            </p>
          </div>
        </div>

        {/* Active Rules */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              Active Rules
            </span>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              {stats.active}
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {stats.activePct}% workspace active
            </p>
          </div>
        </div>

        {/* Disabled Rules */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              Disabled Rules
            </span>
            <XCircle className="h-4 w-4 text-muted-foreground/60" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              {stats.disabled}
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {stats.disabledPct}% rules disabled
            </p>
          </div>
        </div>

        {/* Executions Today placeholder */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              Executions Today
            </span>
            <Clock className="h-4 w-4 text-blue-500" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              0
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Backend metrics coming soon
            </p>
          </div>
        </div>

        {/* Success Rate */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              sa_Success Rate
            </span>
            <Activity className="h-4 w-4 text-emerald-500" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              {stats.successRate}%
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Runs successfully completed
            </p>
          </div>
        </div>

        {/* Failure Rate */}
        <div className="p-4 bg-card border border-border/60 rounded-xl shadow-sm flex flex-col justify-between space-y-3 hover:shadow-md transition-all">
          <div className="flex justify-between items-center">
            <span className="text-[10px] text-muted-foreground font-black uppercase tracking-wider">
              sa_Failure Rate
            </span>
            <AlertTriangle className="h-4 w-4 text-destructive" />
          </div>
          <div>
            <h4 className="text-2xl font-black tracking-tight text-foreground">
              {stats.failureRate}%
            </h4>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Execution failures caught
            </p>
          </div>
        </div>
      </div>

      {/* --- Section 3: Filter, Search & Sorting Panel bar --- */}
      <div className="p-4 bg-muted/20 border border-border/50 rounded-xl flex flex-col md:flex-row md:items-center justify-between gap-4 select-none">
        {/* Search */}
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/60" />
          <input
            type="text"
            placeholder="Search rules by name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-background border border-border/60 rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/10 focus:border-border transition-all"
          />
        </div>

        {/* Filter selectors row */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Status filter */}
          <div className="flex items-center space-x-2">
            <span className="text-[10px] text-muted-foreground uppercase font-bold">
              Status:
            </span>

            <Select
              value={statusFilter}
              onValueChange={(value) =>
                setStatusFilter(value as "ALL" | "ENABLED" | "DISABLED")
              }
            >
              <SelectTrigger className="w-[170px] h-9 text-xs">
                <SelectValue />
              </SelectTrigger>

              <SelectContent>
                <SelectItem value="ALL">All States</SelectItem>
                <SelectItem value="ENABLED">Active Only</SelectItem>
                <SelectItem value="DISABLED">Disabled Only</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Trigger Event filter */}
          <div className="flex items-center space-x-2">
            <span className="text-[10px] text-muted-foreground uppercase font-bold">
              Trigger:
            </span>

            <Select value={eventFilter} onValueChange={setEventFilter}>
              <SelectTrigger className="w-[190px] h-9 text-xs">
                <SelectValue />
              </SelectTrigger>

              <SelectContent>
                <SelectItem value="ALL">All Events</SelectItem>
                <SelectItem value="WORK_ITEM_CREATED">Created</SelectItem>
                <SelectItem value="WORK_ITEM_COMPLETED">Completed</SelectItem>
                <SelectItem value="WORK_ITEM_FAILED">Failed</SelectItem>
                <SelectItem value="WORK_ITEM_REPROCESSED">
                  Reprocessed
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Sort By selectors */}
          <div className="flex items-center space-x-2">
            <span className="text-[10px] text-muted-foreground uppercase font-bold">
              Sort:
            </span>

            <Select value={sortBy} onValueChange={setSortBy}>
              <SelectTrigger className="w-[220px] h-9 text-xs">
                <SelectValue />
              </SelectTrigger>

              <SelectContent>
                <SelectItem value="PRIORITY_ASC">
                  Priority Low → High
                </SelectItem>

                <SelectItem value="PRIORITY_DESC">
                  Priority High → Low
                </SelectItem>

                <SelectItem value="NAME_ASC">Name (A → Z)</SelectItem>

                <SelectItem value="NAME_DESC">Name (Z → A)</SelectItem>

                <SelectItem value="CREATED_DESC">Recently Created</SelectItem>

                <SelectItem value="UPDATED_DESC">Last Updated</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* --- Section 4: Split Columns Operational Grid --- */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Panel 4A: Rules Management Grid list (Left Column) */}
        <div className="lg:col-span-5 space-y-4">
          <h3 className="text-xs font-black uppercase tracking-wider text-muted-foreground pl-1 select-none">
            Configured Rules ({sortedRules.length})
          </h3>

          {sortedRules.length === 0 ? (
            <div className="text-center py-14 px-6 bg-card border border-dashed border-border/60 rounded-2xl select-none flex flex-col items-center justify-center space-y-3">
              <div className="h-12 w-12 rounded-full bg-primary/10 text-primary flex items-center justify-center mb-1">
                <Sliders className="h-6 w-6" />
              </div>
              <div className="space-y-1 max-w-sm">
                <h4 className="text-sm font-extrabold tracking-tight">
                  No matching rules found
                </h4>
                <p className="text-[11px] text-muted-foreground leading-relaxed mb-2">
                  Try adjusting your search queries or active status filter
                  parameters.
                </p>
              </div>
              {/* Clear Filters CTA */}
              <button
                type="button"
                onClick={() => {
                  setSearchQuery("");
                  setStatusFilter("ALL");
                  setEventFilter("ALL");
                  setSortBy("PRIORITY_ASC");
                }}
                className="px-3.5 py-1.5 border border-border rounded-lg text-xs font-bold bg-background hover:bg-muted hover:text-foreground text-muted-foreground transition-all focus:outline-none select-none"
              >
                Clear Filters
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {sortedRules.map((rule) => (
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

                  {/* Structured IF / THEN Panel */}
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

                    {/* THEN Section */}
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

        {/* Panel 4B: Audit Logs & Quick Insights (Right Column) */}
        <div className="lg:col-span-7 space-y-4">
          {/* Quick Insights panel */}
          <div className="bg-card border border-border/60 rounded-xl p-5 shadow-sm select-none space-y-4">
            <header className="flex items-center space-x-2 border-b border-border/30 pb-2">
              <PieChart className="h-4 w-4 text-primary" />
              <h3 className="text-sm font-extrabold tracking-tight">
                Quick Insights
              </h3>
            </header>
            {rules.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground font-semibold">
                No automation insights available
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4 text-xs font-semibold">
                <div className="p-3 bg-muted/40 rounded-lg space-y-1 border border-border/40">
                  <span className="text-muted-foreground text-[10px] uppercase font-bold">
                    Active Rules ratio
                  </span>
                  <p className="text-foreground font-black text-sm">
                    {stats.activePct}% Active / {stats.disabledPct}% Off
                  </p>
                </div>
                <div className="p-3 bg-muted/40 rounded-lg space-y-1 border border-border/40">
                  <span className="text-muted-foreground text-[10px] uppercase font-bold">
                    Average Priority
                  </span>
                  <p className="text-foreground font-black text-sm">
                    {stats.avgPriority} (Scale 1-9999)
                  </p>
                </div>
                <div className="p-3 bg-muted/40 rounded-lg col-span-2 space-y-1 border border-border/40">
                  <span className="text-muted-foreground text-[10px] uppercase font-bold">
                    Highest Priority Rule
                  </span>
                  <p className="text-foreground font-extrabold truncate">
                    {stats.highestPriority
                      ? `${stats.highestPriority.name} (#${stats.highestPriority.priority})`
                      : "None configured"}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Advanced Monitoring & Filter Section */}
          <div className="bg-card border border-border/60 rounded-xl p-5 shadow-sm select-none space-y-4">
            <header className="flex items-center justify-between border-b border-border/30 pb-2">
              <div className="flex items-center space-x-2">
                <Activity className="h-4 w-4 text-primary" />
                <h3 className="text-sm font-extrabold tracking-tight">
                  Audit Log Monitoring
                </h3>
              </div>
              <span className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded font-black">
                {filteredLogs.length} Executions Matched
              </span>
            </header>

            {/* Logs search & filters */}
            <div className="space-y-3">
              {/* Logs search input */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search logs by rule name or document filename..."
                  value={logSearchQuery}
                  onChange={(e) => setLogSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-4 py-2 bg-muted/20 border border-border/60 rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/15"
                />
              </div>

              {/* Advanced multi-filter selectors */}
              <div className="grid grid-cols-3 gap-2 text-xs font-bold">
                <Select
                  value={logStatusFilter}
                  onValueChange={(value) =>
                    setLogStatusFilter(value as "ALL" | "SUCCESS" | "FAILED")
                  }
                >
                  <SelectTrigger className="h-9 text-xs w-full">
                    <SelectValue />
                  </SelectTrigger>

                  <SelectContent>
                    <SelectItem value="ALL">All Statuses</SelectItem>
                    <SelectItem value="SUCCESS">Succeeded Runs</SelectItem>
                    <SelectItem value="FAILED">Failed Runs</SelectItem>
                  </SelectContent>
                </Select>

                <Select value={logRuleFilter} onValueChange={setLogRuleFilter}>
                  <SelectTrigger className="h-9 text-xs w-full">
                    <SelectValue />
                  </SelectTrigger>

                  <SelectContent>
                    <SelectItem value="ALL">All Rules</SelectItem>

                    {rules.map((r) => (
                      <SelectItem key={r.id} value={r.id}>
                        {r.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select
                  value={logDateRangeFilter}
                  onValueChange={(value) =>
                    setLogDateRangeFilter(
                      value as "ALL" | "TODAY" | "7_DAYS" | "30_DAYS"
                    )
                  }
                >
                  <SelectTrigger className="h-9 text-xs w-full">
                    <SelectValue />
                  </SelectTrigger>

                  <SelectContent>
                    <SelectItem value="ALL">All Dates</SelectItem>
                    <SelectItem value="TODAY">Executed Today</SelectItem>
                    <SelectItem value="7_DAYS">Last 7 Days</SelectItem>
                    <SelectItem value="30_DAYS">Last 30 Days</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Filtered Logs KPI summary */}
            <div className="grid grid-cols-5 gap-1.5 text-center text-[10px] font-black uppercase text-muted-foreground border border-border/40 p-2.5 rounded-lg bg-muted/10">
              <div>
                <span className="block text-foreground text-sm font-black">
                  {auditStats.total}
                </span>
                <span>Total</span>
              </div>
              <div>
                <span className="block text-emerald-500 text-sm font-black">
                  {auditStats.successful}
                </span>
                <span>Succeeded</span>
              </div>
              <div>
                <span className="block text-destructive text-sm font-black">
                  {auditStats.failed}
                </span>
                <span>Failed</span>
              </div>
              <div>
                <span className="block text-emerald-500 text-sm font-black">
                  {auditStats.successPct}%
                </span>
                <span>Success Rate</span>
              </div>
              <div>
                <span className="block text-destructive text-sm font-black">
                  {auditStats.failurePct}%
                </span>
                <span>Failure Rate</span>
              </div>
            </div>
          </div>

          {/* Symmetrical timeline list of log executions */}
          <div className="bg-card border border-border/60 rounded-xl overflow-hidden shadow-sm flex flex-col h-[400px]">
            <header className="p-5 border-b border-border/40 bg-muted/5 select-none">
              <h3 className="text-sm font-extrabold tracking-tight">
                Monitoring Timelines
              </h3>
              <p className="text-xs text-muted-foreground font-bold mt-1">
                Timeline trace logs mapping active condition comparisons and
                execution states.
              </p>
            </header>

            {/* List timelines scroller */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar bg-muted/5">
              {filteredLogs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center p-8 select-none text-muted-foreground space-y-2">
                  <Clock className="h-8 w-8 mb-1 opacity-35 animate-pulse" />
                  <p className="text-xs font-semibold">
                    No matching audit trace logs recorded.
                  </p>
                  {(logSearchQuery ||
                    logStatusFilter !== "ALL" ||
                    logRuleFilter !== "ALL" ||
                    logDateRangeFilter !== "ALL") && (
                    <button
                      type="button"
                      onClick={() => {
                        setLogSearchQuery("");
                        setLogStatusFilter("ALL");
                        setLogRuleFilter("ALL");
                        setLogDateRangeFilter("ALL");
                      }}
                      className="text-[10px] border border-border px-2 py-1 bg-background hover:bg-muted rounded font-bold transition-all text-foreground"
                    >
                      Reset Monitor Filters
                    </button>
                  )}
                </div>
              ) : (
                filteredLogs.map((log) => {
                  const isExpanded = expandedLogId === log.id;
                  const isFailed = log.status === "FAILED";

                  return (
                    <div
                      key={log.id}
                      className="rounded-xl border border-border/40 bg-background p-4 shadow-sm transition-all hover:border-border/80 space-y-3"
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

                      {/* Action, Status and Trace summary details */}
                      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/10 pt-2 text-[10px]">
                        <div className="flex items-center space-x-2">
                          <span className="rounded-md bg-primary/10 px-2 py-0.5 font-bold text-primary">
                            {log.action_type}
                          </span>

                          <span
                            className={`rounded-md px-2 py-0.5 font-bold ${
                              isFailed
                                ? "bg-destructive/10 text-destructive"
                                : "bg-emerald-500/10 text-emerald-500"
                            }`}
                          >
                            {isFailed ? "FAILED" : "✓ Successfully Executed"}
                          </span>
                        </div>

                        <span className="text-muted-foreground/80 font-mono">
                          Duration: &lt; 15ms
                        </span>
                      </div>

                      {/* Expanded detailed message */}
                      {log.log_message && (
                        <div className="space-y-2">
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedLogId(isExpanded ? null : log.id)
                            }
                            className="text-[10px] text-primary font-bold hover:underline focus:outline-none flex items-center space-x-1"
                          >
                            <span>
                              {isExpanded
                                ? "Hide Trace Logs"
                                : "Expand Trace Logs"}
                            </span>
                            {isExpanded ? (
                              <ChevronUp className="h-3 w-3" />
                            ) : (
                              <ChevronDown className="h-3 w-3" />
                            )}
                          </button>

                          {isExpanded && (
                            <pre
                              className={`overflow-x-auto rounded-lg border p-3 text-[11px] font-mono leading-relaxed max-h-48 scrollbar
                                ${
                                  isFailed
                                    ? "border-destructive/20 bg-destructive/5 text-destructive"
                                    : "border-border/40 bg-muted/30 text-muted-foreground"
                                }`}
                            >
                              {log.log_message}
                            </pre>
                          )}
                        </div>
                      )}

                      {/* Copy Error & Retry Button */}
                      {isFailed && (
                        <div className="flex justify-end space-x-2 border-t border-border/10 pt-2 select-none">
                          <button
                            type="button"
                            disabled
                            className="rounded-md border border-border px-3 py-1 text-xs bg-muted/50 text-muted-foreground cursor-not-allowed"
                          >
                            Retry (Coming Soon)
                          </button>
                          {log.log_message && (
                            <button
                              type="button"
                              onClick={() => {
                                if (!log.log_message) return;
                                navigator.clipboard.writeText(log.log_message);
                                toast.success("Error copied.");
                              }}
                              className="rounded-md border border-border px-3 py-1 text-xs hover:bg-muted font-bold text-foreground"
                            >
                              Copy Error
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </section>

      {/* --- Section 5: Controlled Rules Creation modal --- */}
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
