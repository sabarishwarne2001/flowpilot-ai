import React, { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { X, Play, Loader2, CheckCircle2, AlertCircle, HelpCircle } from "lucide-react";
import apiClient from "@/services/api/client";
import { automationApi } from "@/services/api/automation";
import type { AutomationRule } from "@/types/automation";

interface RuleTestDialogProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly rule: AutomationRule | null;
}

interface SimplifiedWorkItem {
  readonly id: string;
  readonly original_filename: string;
}

export const RuleTestDialog: React.FC<RuleTestDialogProps> = ({
  isOpen,
  onClose,
  rule,
}) => {
  const [selectedWorkItemId, setSelectedWorkItemId] = useState<string>("");

  // Clear selections when dialog is closed or toggled
  useEffect(() => {
    if (!isOpen) {
      setSelectedWorkItemId("");
    }
  }, [isOpen]);

  // Escape key event boundaries
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isOpen, onClose]);

  // Load standard Work Items belonging to the authenticated account
  const {
    data: workItems = [],
    isLoading: isWorkItemsLoading,
    error: workItemsError,
  } = useQuery({
    queryKey: ["work-items"] as const,
    queryFn: async (): Promise<readonly SimplifiedWorkItem[]> => {
        const response = await apiClient.get<{
            items: SimplifiedWorkItem[];
        }>("/work-items");

        return response.data.items;
    },
    enabled: isOpen,
    staleTime: 1000 * 30,
  });

  // Manual rule testing endpoint mutation trigger
  const {
    mutate: executeRuleTest,
    data: testResult = null,
    isPending: isTesting,
    reset: resetMutation,
  } = useMutation({
    mutationFn: async () => {
      if (!rule) throw new Error("No automation rule selected.");
      return automationApi.testAutomationRule(rule.id, selectedWorkItemId);
    },
    onError: (err: unknown) => {
      if (
        typeof err === "object" &&
        err !== null &&
        "message" in err &&
        typeof (err as { message: unknown }).message === "string"
      ) {
        toast.error((err as { message: string }).message);
      } else {
        toast.error("Failed to execute automation rule test.");
      }
    },
  });

  // Reset internal states on exit
  const handleClose = () => {
    setSelectedWorkItemId("");
    resetMutation();
    onClose();
  };

  if (!isOpen || !rule) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rule-test-title"
    >
      <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-md overflow-hidden flex flex-col animate-scale-in">
        {/* Header block */}
        <header className="h-16 border-b border-border/40 flex items-center justify-between px-6 bg-muted/5 select-none">
          <div className="flex items-center space-x-2">
            <Play className="h-4 w-4 text-primary fill-primary" />
            <h2 id="rule-test-title" className="font-extrabold text-sm uppercase tracking-wider">
              Test Automation Rule
            </h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={isTesting}
            className="p-1.5 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all focus:outline-none"
            aria-label="Close dialog"
          >
            <X className="h-4.5 w-4.5" />
          </button>
        </header>

        {/* Content body */}
        <div className="p-6 space-y-4 max-h-[70vh] overflow-y-auto scrollbar">
          <div className="space-y-1 select-none">
            <span className="text-[10px] text-muted-foreground font-bold uppercase tracking-wider">Active Target Rule</span>
            <h3 className="text-sm font-extrabold leading-snug">{rule.name}</h3>
            <p className="text-xs text-muted-foreground">
              Evaluate events logic for field path <code className="px-1 py-0.5 rounded bg-muted font-mono">{rule.field}</code>.
            </p>
          </div>

          {workItemsError ? (
            <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-center select-none">
              <AlertCircle className="h-5 w-5 text-destructive mx-auto mb-1.5" />
              <p className="text-xs font-bold text-destructive">Failed to load Work Items</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">Please check network connections.</p>
            </div>
          ) : (
            <div className="space-y-2">
              <label htmlFor="test-work-item" className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">
                Select Test Work Item
              </label>

              {isWorkItemsLoading ? (
                <div className="h-10 bg-muted/40 border border-border/50 rounded-lg animate-pulse flex items-center px-4">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground mr-2" />
                  <span className="text-xs text-muted-foreground font-semibold">Syncing work items...</span>
                </div>
              ) : workItems.length === 0 ? (
                <div className="p-6 text-center border border-dashed border-border rounded-xl bg-muted/10 select-none">
                  <HelpCircle className="h-6 w-6 text-muted-foreground/50 mx-auto mb-1.5" />
                  <p className="text-xs font-bold text-muted-foreground">No Work Items found</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Upload a document to run workflow evaluations.</p>
                </div>
              ) : (
                <select
                  id="test-work-item"
                  value={selectedWorkItemId}
                  onChange={(e) => setSelectedWorkItemId(e.target.value)}
                  disabled={isTesting}
                  className="w-full px-3.5 py-2.5 bg-background border border-border rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <option value="" disabled>Select a processed document...</option>
                  {workItems.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.original_filename} ({item.id.slice(0, 8)})
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* Inline Execution Trace Results rendering */}
          {testResult && (
            <div className="border border-border/60 rounded-xl overflow-hidden shadow-sm bg-muted/5 animate-fade-in select-none">
              <header className="px-4 py-3 border-b border-border/40 bg-muted/10 flex justify-between items-center">
                <span className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">Execution Outputs</span>
                <span className="text-[10px] font-mono text-muted-foreground font-bold">{testResult.execution_time_ms} ms</span>
              </header>

              <div className="p-4 space-y-3.5">
                {testResult.matched ? (
                  <div className="flex items-start space-x-3 text-emerald-500">
                    <CheckCircle2 className="h-5 w-5 shrink-0 mt-0.5" />
                    <div>
                      <h4 className="text-xs font-bold leading-snug">Rule Logic Matched</h4>
                      <p className="text-[11px] text-muted-foreground mt-0.5">Conditions evaluated were satisfied correctly.</p>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start space-x-3 text-amber-500">
                    <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
                    <div>
                      <h4 className="text-xs font-bold leading-snug">Rule Logic Did Not Match</h4>
                      <p className="text-[11px] text-muted-foreground mt-0.5">Target values did not satisfy rule condition constraints.</p>
                    </div>
                  </div>
                )}

                <div className="pt-3 border-t border-border/10 space-y-2">
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground font-semibold">Notification Sent</span>
                    <span className={`font-bold ${testResult.notification_sent ? "text-emerald-500" : "text-muted-foreground"}`}>
                      {testResult.notification_sent ? "Yes" : "No"}
                    </span>
                  </div>

                  <div className="flex flex-col gap-1.5 pt-1">
                    <span className="text-[10px] text-muted-foreground font-bold uppercase tracking-wider">Trace details</span>
                    <div className="p-2.5 bg-background border border-border/30 rounded-lg text-[10px] leading-relaxed text-muted-foreground break-all">
                      {testResult.message}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer controls */}
        <footer className="h-16 border-t border-border/40 flex items-center justify-end px-6 bg-muted/5 space-x-3 select-none">
          <button
            type="button"
            onClick={handleClose}
            disabled={isTesting}
            className="px-4 py-2 bg-background border border-border text-muted-foreground hover:text-foreground font-semibold text-xs rounded-lg hover:bg-muted/40 transition-all disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => executeRuleTest()}
            disabled={isTesting || !selectedWorkItemId || workItems.length === 0}
            className="px-5 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none flex items-center justify-center min-w-[100px]"
          >
            {isTesting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
                Testing...
              </>
            ) : (
              "Run Test"
            )}
          </button>
        </footer>
      </div>
    </div>
  );
};
