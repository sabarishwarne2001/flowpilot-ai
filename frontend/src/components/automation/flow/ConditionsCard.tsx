/**
 * ARCH-37 — step 2: only if. Groups of conditions; AND/OR inside a group and
 * between groups. Fields come from the selected triggers (event fields every
 * one of them carries) and from this workspace's recent extractions.
 * Operators are filtered by the field's type.
 */

import React from "react";
import { Filter, Plus, Trash2 } from "lucide-react";

import type { AutomationLogicOperator } from "@/types/automation";
import type { FlowCatalog } from "@/types/automationFlow";
import {
  availableFields,
  commonEventFields,
  fieldFor,
  isValueless,
  operatorLabel,
  operatorsFor,
  uid,
  type CardIssue,
  type ConditionDraft,
  type GroupDraft,
} from "./flowModel";
import { IssueText, StepCard, inputClass } from "./StepCard";

interface ConditionsCardProps {
  readonly catalog: FlowCatalog;
  readonly triggers: readonly string[];
  readonly groups: readonly GroupDraft[];
  readonly groupsOperator: AutomationLogicOperator;
  readonly onChange: (groups: readonly GroupDraft[], groupsOperator: AutomationLogicOperator) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
}

const OperatorToggle: React.FC<{
  readonly value: AutomationLogicOperator;
  readonly onChange: (value: AutomationLogicOperator) => void;
  readonly label: string;
  readonly disabled: boolean;
}> = ({ value, onChange, label, disabled }) => (
  <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-border p-0.5">
    {(["AND", "OR"] as const).map((option) => (
      <button
        key={option}
        type="button"
        role="radio"
        aria-checked={value === option}
        disabled={disabled}
        onClick={() => onChange(option)}
        className={`rounded px-2 py-0.5 text-[10px] font-black ${
          value === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
        }`}
      >
        {option === "AND" ? "ALL" : "ANY"}
      </button>
    ))}
  </div>
);

export const ConditionsCard: React.FC<ConditionsCardProps> = ({
  catalog, triggers, groups, groupsOperator, onChange, issues, disabled,
}) => {
  const eventFields = commonEventFields(catalog, triggers);
  const documentFields = availableFields(catalog, triggers).filter((f) => f.source === "document");
  const limits = catalog.limits;

  const setGroup = (index: number, next: GroupDraft): void =>
    onChange(groups.map((group, i) => (i === index ? next : group)), groupsOperator);

  const setCondition = (g: number, c: number, patch: Partial<ConditionDraft>): void => {
    const group = groups[g];
    if (!group) {return;}
    const conditions = group.conditions.map((condition, i) => {
      if (i !== c) {return condition;}
      const field = patch.field ?? condition.field;
      let operator = patch.operator ?? condition.operator;
      let value = patch.value ?? condition.value;
      if (patch.field !== undefined) {
        const allowed = operatorsFor(catalog, fieldFor(catalog, triggers, field)?.type);
        if (!allowed.includes(operator)) {operator = allowed[0] ?? "EQUALS";}
      }
      if (isValueless(catalog, operator)) {value = "";}
      return { uid: condition.uid, field, operator, value };
    });
    setGroup(g, { ...group, conditions });
  };

  const freshCondition = (): ConditionDraft => {
    const first = eventFields[0] ?? documentFields[0];
    const operators = operatorsFor(catalog, first?.type);
    return { uid: uid("condition"), field: first?.key ?? "", operator: operators[0] ?? "EQUALS", value: "" };
  };

  const addCondition = (g: number): void => {
    const group = groups[g];
    if (!group || group.conditions.length >= limits.conditions_per_group) {return;}
    setGroup(g, { ...group, conditions: [...group.conditions, freshCondition()] });
  };

  const addGroup = (): void => {
    if (groups.length >= limits.groups) {return;}
    // A new group opens with one condition, ready to edit.
    const next: GroupDraft = { uid: uid("group"), logic_operator: "AND", conditions: [freshCondition()] };
    onChange([...groups, next], groupsOperator);
  };

  return (
    <StepCard
      id="flow-step-conditions"
      step={2}
      title="Only if"
      subtitle={
        groups.length === 0
          ? "No conditions: the rule runs every time the trigger fires."
          : "Every group is checked; combine groups with ALL or ANY."
      }
      icon={<Filter className="h-4 w-4" />}
      issues={issues}
      aside={
        groups.length > 1 ? (
          <OperatorToggle
            value={groupsOperator}
            onChange={(value) => onChange(groups, value)}
            label="Combine groups"
            disabled={disabled}
          />
        ) : undefined
      }
    >
      <div className="space-y-3">
        {groups.map((group, g) => (
          <div key={group.uid} className="rounded-lg border border-border/70 bg-muted/20 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-bold text-muted-foreground">
                <span>Group {g + 1}: match</span>
                <OperatorToggle
                  value={group.logic_operator}
                  onChange={(value) => setGroup(g, { ...group, logic_operator: value })}
                  label={`Group ${g + 1} combines with`}
                  disabled={disabled}
                />
              </div>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(groups.filter((_, i) => i !== g), groupsOperator)}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                aria-label={`Remove group ${g + 1}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="space-y-2">
              {group.conditions.map((condition, c) => {
                const field = fieldFor(catalog, triggers, condition.field);
                const operators = operatorsFor(catalog, field?.type);
                const own = issues.filter((issue) => issue.index === g && issue.subIndex === c);
                const byField = (name: string) => own.filter((issue) => issue.field === name);
                const custom = condition.field !== "" && field === undefined && !condition.field.startsWith("event.");
                return (
                  <div key={condition.uid} className="grid grid-cols-1 gap-2 md:grid-cols-12">
                    <div className="md:col-span-5">
                      <select
                        aria-label="Field"
                        disabled={disabled}
                        value={custom ? "__custom__" : condition.field}
                        onChange={(e) =>
                          setCondition(g, c, { field: e.target.value === "__custom__" ? "custom_field" : e.target.value })
                        }
                        className={inputClass(byField("field").length > 0)}
                      >
                        <option value="" disabled>Choose a field…</option>
                        {eventFields.length > 0 && (
                          <optgroup label="From the trigger">
                            {eventFields.map((f) => (
                              <option key={f.key} value={f.key}>{f.label}</option>
                            ))}
                          </optgroup>
                        )}
                        {documentFields.length > 0 && (
                          <optgroup label="From the document">
                            {documentFields.map((f) => (
                              <option key={f.key} value={f.key}>{f.label}</option>
                            ))}
                          </optgroup>
                        )}
                        {triggers.length > 0 && <option value="__custom__">Another document field…</option>}
                      </select>
                      {custom && (
                        <input
                          aria-label="Document field path"
                          disabled={disabled}
                          value={condition.field}
                          onChange={(e) => setCondition(g, c, { field: e.target.value.trim() })}
                          placeholder="e.g. invoice_number"
                          className={`${inputClass(false)} mt-1 font-mono text-xs`}
                        />
                      )}
                      <IssueText issues={byField("field")} />
                    </div>
                    <div className="md:col-span-3">
                      <select
                        aria-label="Comparison"
                        disabled={disabled}
                        value={condition.operator}
                        onChange={(e) => setCondition(g, c, { operator: e.target.value })}
                        className={inputClass(byField("operator").length > 0)}
                      >
                        {operators.map((op) => (
                          <option key={op} value={op}>{operatorLabel(op)}</option>
                        ))}
                      </select>
                      <IssueText issues={byField("operator")} />
                    </div>
                    <div className="md:col-span-3">
                      {!isValueless(catalog, condition.operator) && (
                        <input
                          aria-label="Value"
                          disabled={disabled}
                          type={field?.type === "number" && !["IN", "NOT_IN", "BETWEEN"].includes(condition.operator) ? "number" : "text"}
                          value={condition.value}
                          placeholder={
                            ["IN", "NOT_IN", "BETWEEN", "ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL"].includes(condition.operator)
                              ? "comma, separated"
                              : field?.example || "value"
                          }
                          onChange={(e) => setCondition(g, c, { value: e.target.value })}
                          className={inputClass(byField("value").length > 0)}
                        />
                      )}
                      <IssueText issues={[...byField("value"), ...own.filter((i) => i.field === null)]} />
                    </div>
                    <div className="flex items-start justify-end md:col-span-1">
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => setGroup(g, { ...group, conditions: group.conditions.filter((_, i) => i !== c) })}
                        className="rounded p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        aria-label="Remove condition"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
              <button
                type="button"
                disabled={disabled || triggers.length === 0 || group.conditions.length >= limits.conditions_per_group}
                onClick={() => addCondition(g)}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-bold text-primary hover:bg-primary/10 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" /> Condition
              </button>
            </div>
          </div>
        ))}

        <button
          type="button"
          disabled={disabled || triggers.length === 0 || groups.length >= limits.groups}
          onClick={addGroup}
          className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-bold text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> {groups.length === 0 ? "Add a condition group" : "Add another group"}
        </button>
        {triggers.length === 0 && (
          <p className="text-xs text-muted-foreground">Choose a trigger first; the fields you can test depend on it.</p>
        )}
      </div>
    </StepCard>
  );
};
