import React, { useCallback, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Plus, Trash2 } from "lucide-react";

import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import {
  createAutomationRule,
  updateAutomationRule,
} from "@/services/api/automation";
import { workspaceScope } from "@/services/api/queryKeys";
import type {
  AutomationAction,
  AutomationCondition,
  AutomationEvent,
  AutomationLogicOperator,
  AutomationOperator,
  AutomationRule,
} from "@/types/automation";

const EVENTS: readonly { value: AutomationEvent; label: string }[] = [
  { value: "WORK_ITEM_CREATED", label: "A document is uploaded" },
  { value: "WORK_ITEM_COMPLETED", label: "Processing finishes" },
  { value: "WORK_ITEM_FAILED", label: "Processing fails" },
  { value: "WORK_ITEM_REPROCESSED", label: "A document is reprocessed" },
];

const OPERATORS: readonly { value: AutomationOperator; label: string }[] = [
  { value: "EQUALS", label: "equals" },
  { value: "NOT_EQUALS", label: "does not equal" },
  { value: "CONTAINS", label: "contains" },
  { value: "NOT_CONTAINS", label: "does not contain" },
  { value: "STARTS_WITH", label: "starts with" },
  { value: "ENDS_WITH", label: "ends with" },
  { value: "GREATER_THAN", label: "is greater than" },
  { value: "LESS_THAN", label: "is less than" },
  { value: "GREATER_THAN_OR_EQUAL", label: "is at least" },
  { value: "LESS_THAN_OR_EQUAL", label: "is at most" },
  { value: "BETWEEN", label: "is between" },
  { value: "IN", label: "is one of" },
  { value: "NOT_IN", label: "is not one of" },
  { value: "EXISTS", label: "exists" },
  { value: "IS_EMPTY", label: "is empty" },
  { value: "IS_NOT_EMPTY", label: "is not empty" },
  { value: "ARRAY_CONTAINS_ANY", label: "contains any of" },
  { value: "ARRAY_CONTAINS_ALL", label: "contains all of" },
];

const UNARY_OPERATORS: ReadonlySet<AutomationOperator> = new Set<
  AutomationOperator
>(["EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"]);

export interface RuleEditorProps {
  readonly rule?: AutomationRule | null;
  readonly onSaved?: (rule: AutomationRule) => void;
  readonly onCancel?: () => void;
}

interface DraftAction extends AutomationAction {
  readonly action_type: string;
}

export const RuleEditor: React.FC<RuleEditorProps> = ({
  rule = null,
  onSaved,
  onCancel,
}) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const queryClient = useQueryClient();

  const [name, setName] = useState(rule?.name ?? "");
  const [priority, setPriority] = useState(rule?.priority ?? 100);
  const [event, setEvent] = useState<AutomationEvent>(
    rule?.event ?? "WORK_ITEM_COMPLETED",
  );
  const [isActive, setIsActive] = useState(rule?.is_active ?? true);
  const [logicOperator, setLogicOperator] = useState<AutomationLogicOperator>(
    rule?.logic_operator ?? "AND",
  );
  const [conditions, setConditions] = useState<AutomationCondition[]>(
    rule ? [...rule.conditions] : [],
  );
  const [actions, setActions] = useState<DraftAction[]>(
    rule
      ? rule.actions.map((action) => ({ ...action }))
      : [{ action_type: "SEND_EMAIL", config: {} }],
  );

  const errors = useMemo(() => {
    const found: string[] = [];

    if (name.trim().length === 0) {
      found.push("Give the rule a name.");
    }
    if (name.length > 100) {
      found.push("The name must be 100 characters or fewer.");
    }
    if (priority < 1) {
      found.push("Priority must be at least 1.");
    }
    if (actions.length === 0) {
      found.push("Add at least one action — a rule that does nothing is not a rule.");
    }

    conditions.forEach((condition, index) => {
      if (condition.field.trim().length === 0) {
        found.push(`Condition ${index + 1} has no field.`);
      }
      if (
        !UNARY_OPERATORS.has(condition.operator) &&
        condition.value.trim().length === 0
      ) {
        found.push(`Condition ${index + 1} needs a value.`);
      }
    });

    return found;
  }, [name, priority, actions, conditions]);

  const canSave = errors.length === 0 && Boolean(workspaceId);

  const save = useMutation({
    mutationFn: async (): Promise<AutomationRule> => {
      const payload = {
        name: name.trim(),
        priority,
        event,
        conditions,
        logic_operator: logicOperator,
        actions,
        is_active: isActive,
      };

      return rule
        ? updateAutomationRule(workspaceId, rule.id, payload)
        : createAutomationRule(workspaceId, payload);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({
        queryKey: [...workspaceScope(workspaceId), "automation"],
      });
      onSaved?.(saved);
    },
  });

  const addCondition = useCallback(() => {
    setConditions((current) => [
      ...current,
      { field: "", operator: "EQUALS", value: "" },
    ]);
  }, []);

  const updateCondition = useCallback(
    (index: number, patch: Partial<AutomationCondition>) => {
      setConditions((current) =>
        current.map((condition, position) => {
          if (position !== index) {
            return condition;
          }
          const next = { ...condition, ...patch };
          if (patch.operator && UNARY_OPERATORS.has(patch.operator)) {
            return { ...next, value: "" };
          }
          return next;
        }),
      );
    },
    [],
  );

  const removeCondition = useCallback((index: number) => {
    setConditions((current) =>
      current.filter((_, position) => position !== index),
    );
  }, []);

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <div>
          <label htmlFor="rule-name" className="block text-sm font-medium">
            Name
          </label>
          <input
            id="rule-name"
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
            placeholder="Notify finance when an invoice arrives"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label
              htmlFor="rule-priority"
              className="block text-sm font-medium"
            >
              Priority
            </label>
            <input
              id="rule-priority"
              type="number"
              min={1}
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value) || 1)}
              className="mt-1 w-24 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Lower runs first
            </p>
          </div>

          <label className="flex items-center gap-2 pb-2 text-sm">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              className="h-4 w-4 rounded border-border"
            />
            Active
          </label>
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium">When</h3>
        <select
          value={event}
          onChange={(e) => setEvent(e.target.value as AutomationEvent)}
          aria-label="Trigger event"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {EVENTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </section>

      <section>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">And if</h3>

          {conditions.length > 1 && (
            <div
              role="group"
              aria-label="Condition logic"
              className="flex items-center gap-1"
            >
              {(["AND", "OR"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setLogicOperator(option)}
                  aria-pressed={logicOperator === option}
                  className={[
                    "rounded border px-2 py-0.5 text-xs",
                    logicOperator === option
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-muted",
                  ].join(" ")}
                >
                  {option === "AND" ? "Match all" : "Match any"}
                </button>
              ))}
            </div>
          )}
        </div>

        {conditions.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">
            No conditions — the rule runs on every matching event.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {conditions.map((condition, index) => {
              const unary = UNARY_OPERATORS.has(condition.operator);

              return (
                <li
                  key={index}
                  className="flex flex-wrap items-start gap-2 rounded-md border border-border bg-card p-2"
                >
                  <input
                    value={condition.field}
                    onChange={(e) =>
                      updateCondition(index, { field: e.target.value })
                    }
                    placeholder="field"
                    aria-label={`Condition ${index + 1} field`}
                    className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 text-sm"
                  />

                  <select
                    value={condition.operator}
                    onChange={(e) =>
                      updateCondition(index, {
                        operator: e.target.value as AutomationOperator,
                      })
                    }
                    aria-label={`Condition ${index + 1} operator`}
                    className="rounded border border-border bg-background px-2 py-1.5 text-sm"
                  >
                    {OPERATORS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>

                  {!unary && (
                    <input
                      value={condition.value}
                      onChange={(e) =>
                        updateCondition(index, { value: e.target.value })
                      }
                      placeholder="value"
                      aria-label={`Condition ${index + 1} value`}
                      className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 text-sm"
                    />
                  )}

                  <button
                    type="button"
                    onClick={() => removeCondition(index)}
                    aria-label={`Remove condition ${index + 1}`}
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <button
          type="button"
          onClick={addCondition}
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted"
        >
          <Plus className="h-3.5 w-3.5" />
          Add condition
        </button>
      </section>

      <section>
        <h3 className="text-sm font-medium">Then</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Actions run in order.
        </p>

        <ul className="mt-2 space-y-2">
          {actions.map((action, index) => (
            <li
              key={index}
              className="flex items-center gap-2 rounded-md border border-border bg-card p-2"
            >
              <span className="text-xs text-muted-foreground">{index + 1}.</span>

              <select
                value={action.action_type}
                onChange={(e) =>
                  setActions((current) =>
                    current.map((item, position) =>
                      position === index
                        ? { ...item, action_type: e.target.value }
                        : item,
                    ),
                  )
                }
                aria-label={`Action ${index + 1} type`}
                className="rounded border border-border bg-background px-2 py-1.5 text-sm"
              >
                <option value="SEND_EMAIL">Send an email</option>
              </select>

              {actions.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    setActions((current) =>
                      current.filter((_, position) => position !== index),
                    )
                  }
                  aria-label={`Remove action ${index + 1}`}
                  className="ml-auto rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </li>
          ))}
        </ul>

        <button
          type="button"
          onClick={() =>
            setActions((current) => [
              ...current,
              { action_type: "SEND_EMAIL", config: {} },
            ])
          }
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted"
        >
          <Plus className="h-3.5 w-3.5" />
          Add action
        </button>
      </section>

      {errors.length > 0 && (
        <ul className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          {errors.map((error) => (
            <li
              key={error}
              className="flex items-start gap-1.5 text-xs text-amber-800"
            >
              <AlertTriangle
                className="mt-0.5 h-3.5 w-3.5 shrink-0"
                aria-hidden="true"
              />
              {error}
            </li>
          ))}
        </ul>
      )}

      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          The rule couldn&apos;t be saved. Nothing was changed.
        </p>
      )}

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={save.isPending}
            className="rounded-md border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={!canSave || save.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {rule ? "Save changes" : "Create rule"}
        </button>
      </div>
    </div>
  );
};

export default RuleEditor;
