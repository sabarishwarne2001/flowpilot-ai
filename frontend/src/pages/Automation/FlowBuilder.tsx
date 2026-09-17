/**
 * ARCH-37 — the Enterprise Flow Builder.
 *
 * Replaces RuleForm (one trigger dropdown, one hardcoded "Send SMTP Email"
 * action, a field list written for resumes and invoices). A vertical step
 * builder: When -> Only if -> Then (-> Otherwise), with a summary rail.
 *
 * Everything offered comes from the catalog endpoint. Saving sends the whole
 * rule; the server validates it and returns refusals located by `loc`, which
 * `issuesFromError` places on the card that caused them. Local checks mirror
 * the server's so most mistakes show before a round trip.
 *
 * Keyboard: Ctrl/Cmd+S and Ctrl/Cmd+Enter save, Esc closes. Closing with
 * unsaved changes asks first, and so does leaving the page.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Settings2, X } from "lucide-react";
import { toast } from "sonner";

import { ActionsCard } from "@/components/automation/flow/ActionsCard";
import { ConditionsCard } from "@/components/automation/flow/ConditionsCard";
import {
  draftFromRule,
  emptyDraft,
  issuesFromError,
  issuesFor,
  localIssues,
  payloadFromDraft,
  sameDraft,
  summarize,
  type CardIssue,
  type FlowDraft,
} from "@/components/automation/flow/flowModel";
import { IssueText, StepCard, inputClass } from "@/components/automation/flow/StepCard";
import { SummaryRail } from "@/components/automation/flow/SummaryRail";
import { TriggerCard } from "@/components/automation/flow/TriggerCard";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { automationApi } from "@/services/api/automation";
import { automationKeys } from "@/services/api/queryKeys";
import type { AutomationRule } from "@/types/automation";

interface FlowBuilderProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onSaveSuccess: () => void;
  readonly ruleToEdit?: AutomationRule | null;
  readonly ruleToDuplicate?: AutomationRule | null;
  readonly existingRules?: readonly AutomationRule[];
}

export const FlowBuilder: React.FC<FlowBuilderProps> = ({
  isOpen,
  onClose,
  onSaveSuccess,
  ruleToEdit = null,
  ruleToDuplicate = null,
  existingRules = [],
}) => {
  const workspaceId = useActiveWorkspaceId();
  const queryClient = useQueryClient();
  const isEdit = ruleToEdit !== null;
  const source = ruleToEdit ?? ruleToDuplicate;

  const catalogQuery = useQuery({
    queryKey: automationKeys.catalog(workspaceId ?? ""),
    queryFn: () => automationApi.getAutomationCatalog(workspaceId ?? ""),
    enabled: isOpen && Boolean(workspaceId),
    staleTime: 60_000,
  });
  const catalog = catalogQuery.data;

  const [draft, setDraft] = useState<FlowDraft>(emptyDraft);
  const [initial, setInitial] = useState<FlowDraft>(emptyDraft);
  const [serverIssues, setServerIssues] = useState<readonly CardIssue[]>([]);
  const [attempted, setAttempted] = useState(false);
  const openedFor = useRef<string | null>(null);

  // Load the draft once per opening, after the catalog arrives (legacy action
  // names are resolved through the catalog's aliases).
  useEffect(() => {
    if (!isOpen) {
      openedFor.current = null;
      return;
    }
    if (!catalog) {return;}
    const key = `${source?.id ?? "new"}:${isEdit ? "edit" : "dup"}`;
    if (openedFor.current === key) {return;}
    openedFor.current = key;
    const next = source ? draftFromRule(source, catalog, isEdit ? "edit" : "duplicate") : emptyDraft();
    setDraft(next);
    setInitial(next);
    setServerIssues([]);
    setAttempted(false);
  }, [isOpen, catalog, source, isEdit]);

  const update = useCallback((patch: Partial<FlowDraft>): void => {
    setDraft((current) => ({ ...current, ...patch }));
    setServerIssues([]);
  }, []);

  const dirty = catalog ? !sameDraft(draft, initial, catalog) : false;

  const duplicateName = useMemo(() => {
    const name = draft.name.trim().toLowerCase();
    return Boolean(name) && existingRules.some((r) => r.name.trim().toLowerCase() === name && r.id !== ruleToEdit?.id);
  }, [draft.name, existingRules, ruleToEdit]);

  const issues = useMemo<CardIssue[]>(() => {
    const local = catalog ? localIssues(draft, catalog) : [];
    if (duplicateName) {
      local.push({ card: "general", index: null, subIndex: null, field: "name", message: "Another rule already has this name." });
    }
    return [...serverIssues, ...local.filter((l) => !serverIssues.some((s) => s.card === l.card && s.message === l.message))];
  }, [catalog, draft, duplicateName, serverIssues]);
  const shown = attempted ? issues : serverIssues;

  const save = useMutation({
    mutationFn: async () => {
      if (!workspaceId || !catalog) {throw new Error("The workspace is not ready yet.");}
      const payload = payloadFromDraft(draft, catalog);
      return ruleToEdit
        ? automationApi.updateAutomationRule(workspaceId, ruleToEdit.id, payload)
        : automationApi.createAutomationRule(workspaceId, payload);
    },
    onSuccess: async (rule) => {
      toast.success(isEdit ? `Saved “${rule.name}”.` : `Created “${rule.name}”.`);
      if (workspaceId) {await queryClient.invalidateQueries({ queryKey: automationKeys.rules(workspaceId) });}
      setInitial(draft);
      onSaveSuccess();
      onClose();
    },
    onError: (error: unknown) => {
      const located = issuesFromError(error);
      if (located && located.length) {
        setServerIssues(located);
        toast.error("The rule was not saved. See the highlighted steps.");
      } else {
        toast.error(error instanceof Error ? error.message : "The rule could not be saved.");
      }
    },
  });

  const submit = useCallback((): void => {
    setAttempted(true);
    if (save.isPending) {return;}
    if (issues.length > 0) {
      toast.error("Fix the highlighted steps before saving.");
      const first = issues[0];
      if (first) {document.getElementById(`flow-step-${first.card}`)?.scrollIntoView({ behavior: "smooth", block: "start" });}
      return;
    }
    save.mutate();
  }, [issues, save]);

  const requestClose = useCallback((): void => {
    if (save.isPending) {return;}
    if (dirty && !window.confirm("Discard your unsaved changes to this rule?")) {return;}
    onClose();
  }, [dirty, onClose, save.isPending]);

  useEffect(() => {
    if (!isOpen) {return undefined;}
    const onKey = (event: KeyboardEvent): void => {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && (event.key === "s" || event.key === "S" || event.key === "Enter")) {
        event.preventDefault();
        submit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        requestClose();
      }
    };
    const onUnload = (event: BeforeUnloadEvent): void => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [isOpen, submit, requestClose, dirty]);

  if (!isOpen) {return null;}

  const busy = save.isPending;
  const hasConditions = draft.groups.some((g) => g.conditions.length > 0);
  const general = issuesFor(shown, "general");

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/40 p-0 backdrop-blur-sm sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="flow-builder-title"
    >
      <div className="flex w-full max-w-6xl flex-col overflow-hidden border border-border bg-background shadow-2xl sm:rounded-xl">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-card px-5">
          <h2 id="flow-builder-title" className="text-sm font-extrabold uppercase tracking-wider">
            {isEdit ? "Edit automation" : ruleToDuplicate ? "Duplicate automation" : "New automation"}
          </h2>
          <button
            type="button"
            onClick={requestClose}
            disabled={busy}
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Close the flow builder"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        {!catalog ? (
          <div className="flex flex-1 items-center justify-center p-10 text-sm text-muted-foreground" role="status">
            {catalogQuery.isError ? (
              <span className="text-destructive">
                The automation catalog could not be loaded.{" "}
                <button type="button" className="underline" onClick={() => void catalogQuery.refetch()}>Retry</button>
              </span>
            ) : (
              <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Loading triggers and actions…</span>
            )}
          </div>
        ) : (
          <div className="grid flex-1 grid-cols-1 gap-5 overflow-y-auto p-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0 space-y-5">
              <StepCard
                id="flow-step-general"
                step={0}
                title="Details"
                subtitle="Name the rule and decide how it behaves when an action fails."
                icon={<Settings2 className="h-4 w-4" />}
                issues={general.filter((i) => i.field === null)}
              >
                <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
                  <div className="md:col-span-6">
                    <label htmlFor="flow-name" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Name</label>
                    <input
                      id="flow-name"
                      autoFocus
                      disabled={busy}
                      maxLength={100}
                      value={draft.name}
                      placeholder="e.g. Escalate disputed invoices over $10k"
                      onChange={(e) => update({ name: e.target.value })}
                      className={inputClass(general.some((i) => i.field === "name"))}
                    />
                    <IssueText issues={general.filter((i) => i.field === "name")} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="flow-priority" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Priority</label>
                    <input
                      id="flow-priority"
                      type="number"
                      min={1}
                      max={9999}
                      disabled={busy}
                      value={Number.isFinite(draft.priority) ? draft.priority : ""}
                      onChange={(e) => update({ priority: Number(e.target.value) })}
                      className={inputClass(general.some((i) => i.field === "priority"))}
                    />
                    <IssueText issues={general.filter((i) => i.field === "priority")} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="flow-on-error" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">On failure</label>
                    <select
                      id="flow-on-error"
                      disabled={busy}
                      value={draft.on_error}
                      onChange={(e) => update({ on_error: e.target.value === "CONTINUE" ? "CONTINUE" : "HALT" })}
                      className={inputClass(false)}
                    >
                      <option value="HALT">Stop the rule</option>
                      <option value="CONTINUE">Keep going</option>
                    </select>
                  </div>
                  <div className="flex items-end md:col-span-2">
                    <label className="inline-flex cursor-pointer items-center gap-2 pb-2 text-sm font-semibold">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary"
                        disabled={busy}
                        checked={draft.is_active}
                        onChange={(e) => update({ is_active: e.target.checked })}
                      />
                      Active
                    </label>
                  </div>
                </div>
              </StepCard>

              <TriggerCard
                catalog={catalog}
                selected={draft.triggers}
                onChange={(triggers) => update({ triggers })}
                issues={issuesFor(shown, "trigger")}
                disabled={busy}
              />
              <ConditionsCard
                catalog={catalog}
                triggers={draft.triggers}
                groups={draft.groups}
                groupsOperator={draft.groups_operator}
                onChange={(groups, groups_operator) => update({ groups, groups_operator })}
                issues={issuesFor(shown, "conditions")}
                disabled={busy}
              />
              <ActionsCard
                catalog={catalog}
                card="actions"
                step={3}
                triggers={draft.triggers}
                actions={draft.actions}
                onChange={(actions) => update({ actions })}
                issues={issuesFor(shown, "actions")}
                disabled={busy}
                hasConditions={hasConditions}
              />
              <ActionsCard
                catalog={catalog}
                card="else"
                step={4}
                triggers={draft.triggers}
                actions={draft.else_actions}
                onChange={(else_actions) => update({ else_actions })}
                issues={issuesFor(shown, "else")}
                disabled={busy}
                hasConditions={hasConditions}
              />
            </div>

            <SummaryRail
              sentence={summarize(draft, catalog)}
              issues={shown}
              dirty={dirty}
              saving={busy}
              isEdit={isEdit}
              onSave={submit}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default FlowBuilder;
