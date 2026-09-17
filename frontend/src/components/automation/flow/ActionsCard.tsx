/**
 * ARCH-37 — step 3 ("Then") and the optional "Otherwise" branch.
 *
 * An ordered list. Each action runs after the one above it succeeds; a failed
 * action, or calibrated autonomy holding the document, stops the ones below.
 * Actions the plan, the add-on or the author's role does not allow are shown
 * disabled with the reason; actions the selected trigger cannot run are
 * hidden from the picker and flagged if already present.
 */

import React, { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GitBranch, Lock, Play, Plus, Trash2 } from "lucide-react";

import type { FlowAction, FlowCatalog } from "@/types/automationFlow";
import { ActionConfigForm } from "./ActionConfigForm";
import {
  defaultConfig,
  excludedActions,
  findAction,
  uid,
  type ActionDraft,
  type CardIssue,
  type CardKey,
} from "./flowModel";
import { IssueText, StepCard } from "./StepCard";

interface ActionsCardProps {
  readonly catalog: FlowCatalog;
  readonly card: Extract<CardKey, "actions" | "else">;
  readonly step: number;
  readonly triggers: readonly string[];
  readonly actions: readonly ActionDraft[];
  readonly onChange: (next: readonly ActionDraft[]) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
  readonly hasConditions: boolean;
}

export const ActionsCard: React.FC<ActionsCardProps> = ({
  catalog, card, step, triggers, actions, onChange, issues, disabled, hasConditions,
}) => {
  const [picking, setPicking] = useState<string>("");
  const excluded = excludedActions(catalog, triggers);
  const limit = catalog.limits.actions_per_branch;
  const isElse = card === "else";

  const grouped = useMemo(() => {
    const groups = new Map<string, FlowAction[]>();
    for (const action of catalog.actions) {
      if (excluded.has(action.type)) {continue;}
      const list = groups.get(action.category) ?? [];
      list.push(action);
      groups.set(action.category, list);
    }
    return Array.from(groups.entries());
  }, [catalog.actions, excluded]);

  const add = (type: string): void => {
    const action = findAction(catalog, type);
    if (!action || !action.available || actions.length >= limit) {return;}
    onChange([...actions, { uid: uid("action"), action_type: action.type, config: defaultConfig(action) }]);
    setPicking("");
  };

  const move = (index: number, delta: number): void => {
    const target = index + delta;
    if (target < 0 || target >= actions.length) {return;}
    const next = [...actions];
    const [item] = next.splice(index, 1);
    if (item) {next.splice(target, 0, item);}
    onChange(next);
  };

  const replace = (index: number, entry: ActionDraft): void =>
    onChange(actions.map((a, i) => (i === index ? entry : a)));

  if (isElse && !hasConditions && actions.length === 0) {
    return null;
  }

  return (
    <StepCard
      id={`flow-step-${card}`}
      step={step}
      title={isElse ? "Otherwise" : "Then"}
      subtitle={
        isElse
          ? "Runs instead when the conditions are not met."
          : "Runs in order. A failure, or a document held by calibrated autonomy, stops the actions after it."
      }
      icon={isElse ? <GitBranch className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      issues={issues}
    >
      <ol className="space-y-3">
        {actions.map((entry, index) => {
          const action = findAction(catalog, entry.action_type);
          const own = issues.filter((issue) => issue.index === index);
          const loose = own.filter((issue) => issue.field === null);
          return (
            <li
              key={entry.uid}
              className={`rounded-lg border p-3 ${own.length ? "border-destructive/60" : "border-border"} bg-background`}
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-bold">
                    <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">#{index + 1}</span>
                    {action?.label ?? entry.action_type}
                    {action && <span className="text-[10px] font-semibold uppercase text-muted-foreground">{action.category}</span>}
                    {action && !action.available && (
                      <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">
                        <Lock className="h-3 w-3" aria-hidden="true" /> {action.unavailable_reason}
                      </span>
                    )}
                  </p>
                  {action && <p className="mt-0.5 text-xs text-muted-foreground">{action.description}</p>}
                  <IssueText issues={loose} />
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button type="button" disabled={disabled || index === 0} onClick={() => move(index, -1)}
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-40" aria-label={`Move action ${index + 1} up`}>
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" disabled={disabled || index === actions.length - 1} onClick={() => move(index, 1)}
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-40" aria-label={`Move action ${index + 1} down`}>
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" disabled={disabled} onClick={() => onChange(actions.filter((_, i) => i !== index))}
                    className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" aria-label={`Remove action ${index + 1}`}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {action ? (
                <ActionConfigForm
                  catalog={catalog}
                  action={action}
                  triggers={triggers}
                  config={entry.config}
                  onChange={(config) => replace(index, { ...entry, config })}
                  issues={own}
                  disabled={disabled}
                  idPrefix={`${card}-${entry.uid}`}
                />
              ) : (
                <p className="text-xs text-destructive">This action is no longer offered. Remove it to save the rule.</p>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`${card}-picker`} className="sr-only">Add an action</label>
        <select
          id={`${card}-picker`}
          value={picking}
          disabled={disabled || actions.length >= limit}
          onChange={(e) => {
            setPicking(e.target.value);
            add(e.target.value);
          }}
          className="rounded-lg border border-dashed border-border bg-background px-3 py-1.5 text-xs font-bold text-muted-foreground"
        >
          <option value="">{actions.length >= limit ? `Limit of ${limit} actions reached` : "+ Add an action…"}</option>
          {grouped.map(([category, list]) => (
            <optgroup key={category} label={category}>
              {list.map((action) => (
                <option key={action.type} value={action.type} disabled={!action.available}>
                  {action.label}{action.available ? "" : ` — ${action.unavailable_reason ?? "unavailable"}`}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {actions.length === 0 && !isElse && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Plus className="h-3 w-3" aria-hidden="true" /> A rule needs at least one action.
          </span>
        )}
      </div>
    </StepCard>
  );
};
