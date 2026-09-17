/**
 * ARCH-37 — step 1: when. Every entry is a catalog trigger; an entry the plan
 * does not include is shown, locked, with the reason.
 */

import React, { useMemo } from "react";
import { Lock, Zap } from "lucide-react";

import type { FlowCatalog, FlowTrigger } from "@/types/automationFlow";
import type { CardIssue } from "./flowModel";
import { IssueText, StepCard } from "./StepCard";

interface TriggerCardProps {
  readonly catalog: FlowCatalog;
  readonly selected: readonly string[];
  readonly onChange: (next: readonly string[]) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
}

export const TriggerCard: React.FC<TriggerCardProps> = ({ catalog, selected, onChange, issues, disabled }) => {
  const byCategory = useMemo(() => {
    const groups = new Map<string, FlowTrigger[]>();
    for (const trigger of catalog.triggers) {
      const list = groups.get(trigger.category) ?? [];
      list.push(trigger);
      groups.set(trigger.category, list);
    }
    return Array.from(groups.entries());
  }, [catalog.triggers]);

  const limit = catalog.limits.triggers;
  const toggle = (key: string): void => {
    if (selected.includes(key)) {
      onChange(selected.filter((k) => k !== key));
    } else if (selected.length < limit) {
      onChange([...selected, key]);
    }
  };

  return (
    <StepCard
      id="flow-step-trigger"
      step={1}
      title="When"
      subtitle={`Choose what starts this rule. Pick up to ${limit}; the rule runs when any of them happens.`}
      icon={<Zap className="h-4 w-4" />}
      issues={issues}
    >
      <div className="space-y-4">
        {byCategory.map(([category, triggers]) => (
          <fieldset key={category} className="space-y-2">
            <legend className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">{category}</legend>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {triggers.map((trigger) => {
                const checked = selected.includes(trigger.key);
                const index = selected.indexOf(trigger.key);
                const locked = !trigger.available;
                const full = !checked && selected.length >= limit;
                const own = issues.filter((issue) => issue.index !== null && issue.index === index);
                return (
                  <label
                    key={trigger.key}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                      checked ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                    } ${locked || full || disabled ? "cursor-not-allowed opacity-60" : ""}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 accent-primary"
                      checked={checked}
                      disabled={disabled || (!checked && (locked || full))}
                      onChange={() => toggle(trigger.key)}
                      aria-describedby={`trigger-desc-${trigger.key}`}
                    />
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
                        {trigger.label}
                        {locked && (
                          <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">
                            <Lock className="h-3 w-3" aria-hidden="true" /> Not in your plan
                          </span>
                        )}
                        {trigger.runs_during_review && (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 dark:text-amber-400">
                            Runs during review
                          </span>
                        )}
                      </span>
                      <span id={`trigger-desc-${trigger.key}`} className="mt-0.5 block text-xs text-muted-foreground">
                        {trigger.description}
                      </span>
                      <IssueText issues={own} />
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </StepCard>
  );
};
