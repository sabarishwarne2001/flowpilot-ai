import React, { useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Lock, Plus, X } from "lucide-react";

import { fromMicros, toMicros } from "@/components/helm/EditableReadback";
import { useHelm } from "@/components/helm/HelmContext";
import { organizationBillingPath } from "@/routes/tenantPaths";
import { getHelmLibrary, helmKeys } from "@/services/api/helm";
import type { ConditionEditorSpec, ConditionField, ConditionOp, CustomCondition } from "@/types/helm";

/**
 * The custom condition's rows editor (Enterprise): a name, "all" or "any", one to eight rows of
 * `fact · comparison · value`, and what Helm does with a matching invoice. The facts and their
 * comparisons come from the server (the library's `editor`), so the rows can only say what the
 * validator accepts; the server still checks every change and says any problem in words. Typed
 * values are sent when the field is left (or Enter is pressed), never on every key.
 *
 * Below Enterprise the rule shows read-only, with an upgrade note.
 */

export const CUSTOM_TEMPLATE = "custom_condition";

type Params = Record<string, unknown>;

interface Row {
  readonly id: number;
  readonly field: string;
  readonly op: ConditionOp;
  /** Money's amount, a number of days, or text (a comma-separated list for "is one of"). */
  readonly text: string;
  readonly currency: string;
  readonly flag: boolean;
  /** A choice field's chosen values (one, or several for "is one of"). */
  readonly picked: readonly string[];
}

interface Money {
  amount_micros: number;
  currency: string;
}

const isMoney = (value: unknown): value is Money =>
  typeof value === "object" && value !== null && "amount_micros" in value && "currency" in value;

const rowFrom = (id: number, condition: CustomCondition, field: ConditionField | undefined): Row => {
  const value = condition.value;
  const base: Row = { id, field: condition.field, op: condition.op, text: "", currency: "INR", flag: true, picked: [] };
  if (!field) {
    return base;
  }
  switch (field.type) {
    case "money":
      return isMoney(value) ? { ...base, text: fromMicros(value.amount_micros), currency: value.currency } : base;
    case "number":
      return { ...base, text: typeof value === "number" ? String(value) : "" };
    case "bool":
      return { ...base, flag: value !== false };
    case "choice":
      return { ...base, picked: Array.isArray(value) ? value.map(String) : value ? [String(value)] : [] };
    default:
      return { ...base, text: Array.isArray(value) ? value.join(", ") : typeof value === "string" ? value : "" };
  }
};

/** A row as the server stores it; a value that cannot be read is sent as null, and the server says what it needs. */
const conditionOf = (row: Row, field: ConditionField | undefined): CustomCondition => {
  const text = row.text.trim();
  let value: unknown = null;
  switch (field?.type) {
    case "money": {
      const micros = toMicros(text);
      value = micros !== null && /^[A-Za-z]{3}$/.test(row.currency.trim())
        ? { amount_micros: micros, currency: row.currency.trim().toUpperCase() }
        : null;
      break;
    }
    case "number":
      value = /^-?\d+$/.test(text) ? Number(text) : null;
      break;
    case "bool":
      value = row.flag;
      break;
    case "choice":
      value = row.op === "in" ? (row.picked.length > 0 ? [...row.picked] : null) : (row.picked[0] ?? null);
      break;
    default: {
      if (row.op === "in") {
        const items = text.split(",").map((item) => item.trim()).filter(Boolean);
        value = items.length > 0 ? items : null;
      } else {
        value = text || null;
      }
    }
  }
  return { field: row.field, op: row.op, value };
};

/** A fresh row for a field: its first comparison, and a value of the right kind. */
const freshRow = (id: number, field: ConditionField, currency: string): Row => ({
  id,
  field: field.key,
  op: field.ops[0]?.op ?? "=",
  text: "",
  currency,
  flag: true,
  picked: field.choices[0] ? [field.choices[0].value] : [],
});

const ConditionRow: React.FC<{
  readonly row: Row;
  readonly index: number;
  readonly spec: ConditionEditorSpec;
  readonly disabled: boolean;
  readonly canRemove: boolean;
  readonly onChange: (next: Row, commit: boolean) => void;
  readonly onCommit: () => void;
  readonly onRemove: () => void;
}> = ({ row, index, spec, disabled, canRemove, onChange, onCommit, onRemove }) => {
  const field = spec.fields.find((f) => f.key === row.field);
  const n = index + 1;
  const commitOnEnter = (event: React.KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      onCommit();
    }
  };

  const value = (): React.ReactNode => {
    if (!field) {
      return null;
    }
    switch (field.type) {
      case "money":
        return (
          <span className="helm-condition-value">
            <input
              className="helm-input helm-figure"
              inputMode="decimal"
              aria-label={`Amount for condition ${n}`}
              placeholder="500000"
              value={row.text}
              disabled={disabled}
              onChange={(event) => onChange({ ...row, text: event.target.value }, false)}
              onBlur={onCommit}
              onKeyDown={commitOnEnter}
            />
            <input
              className="helm-input helm-figure helm-condition-currency uppercase"
              aria-label={`Currency for condition ${n}`}
              maxLength={3}
              value={row.currency}
              disabled={disabled}
              onChange={(event) => onChange({ ...row, currency: event.target.value }, false)}
              onBlur={onCommit}
              onKeyDown={commitOnEnter}
            />
          </span>
        );
      case "number":
        return (
          <span className="helm-condition-value">
            <input
              className="helm-input helm-figure"
              inputMode="numeric"
              aria-label={`Number of ${field.unit ?? "days"} for condition ${n}`}
              placeholder="5"
              value={row.text}
              disabled={disabled}
              onChange={(event) => onChange({ ...row, text: event.target.value }, false)}
              onBlur={onCommit}
              onKeyDown={commitOnEnter}
            />
            {field.unit ? <span className="helm-muted text-xs">{field.unit}</span> : null}
          </span>
        );
      case "bool":
        return (
          <select
            className="helm-input"
            aria-label={`Value for condition ${n}`}
            value={row.flag ? "yes" : "no"}
            disabled={disabled}
            onChange={(event) => onChange({ ...row, flag: event.target.value === "yes" }, true)}
          >
            <option value="yes">yes</option>
            <option value="no">no</option>
          </select>
        );
      case "choice":
        return row.op === "in" ? (
          <span className="helm-condition-value flex-wrap" role="group" aria-label={`Values for condition ${n}`}>
            {field.choices.map((choice) => (
              <label key={choice.value} className="helm-chip cursor-pointer gap-1.5 py-1">
                <input
                  type="checkbox"
                  checked={row.picked.includes(choice.value)}
                  disabled={disabled}
                  onChange={(event) =>
                    onChange(
                      {
                        ...row,
                        picked: event.target.checked
                          ? [...row.picked, choice.value]
                          : row.picked.filter((picked) => picked !== choice.value),
                      },
                      true,
                    )
                  }
                />
                {choice.label}
              </label>
            ))}
          </span>
        ) : (
          <select
            className="helm-input"
            aria-label={`Value for condition ${n}`}
            value={row.picked[0] ?? ""}
            disabled={disabled}
            onChange={(event) => onChange({ ...row, picked: [event.target.value] }, true)}
          >
            {field.choices.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        );
      default:
        return (
          <input
            className="helm-input"
            aria-label={`Value for condition ${n}`}
            placeholder={row.op === "in" ? "Names, separated by commas" : ""}
            maxLength={row.op === "in" ? 2000 : 200}
            value={row.text}
            disabled={disabled}
            onChange={(event) => onChange({ ...row, text: event.target.value }, false)}
            onBlur={onCommit}
            onKeyDown={commitOnEnter}
          />
        );
    }
  };

  return (
    <li className="helm-condition-row" data-testid="helm-condition-row">
      <select
        className="helm-input"
        aria-label={`Fact for condition ${n}`}
        value={row.field}
        disabled={disabled}
        onChange={(event) => {
          const next = spec.fields.find((f) => f.key === event.target.value);
          if (next) {
            onChange(freshRow(row.id, next, row.currency), true);
          }
        }}
      >
        {spec.fields.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}
          </option>
        ))}
      </select>
      <select
        className="helm-input"
        aria-label={`Comparison for condition ${n}`}
        value={row.op}
        disabled={disabled}
        onChange={(event) => {
          const op = event.target.value as ConditionOp;
          const picked = op === "in" || row.picked.length === 0 ? row.picked : row.picked.slice(0, 1);
          onChange({ ...row, op, picked }, true);
        }}
      >
        {(field?.ops ?? []).map((option) => (
          <option key={option.op} value={option.op}>
            {option.words}
          </option>
        ))}
      </select>
      {value()}
      <button
        type="button"
        className="helm-btn helm-condition-remove"
        disabled={disabled || !canRemove}
        onClick={onRemove}
        aria-label={`Remove condition ${n}`}
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </li>
  );
};

type EditorProps = {
  readonly params: Params;
  readonly onChange: (param: string, value: unknown) => void;
  readonly disabled?: boolean;
};

export const ConditionsEditor: React.FC<EditorProps> = (props) => {
  const { organizationId } = useHelm();
  const library = useQuery({ queryKey: helmKeys.library(organizationId), queryFn: () => getHelmLibrary(organizationId) });
  const spec = library.data?.templates.find((t) => t.key === CUSTOM_TEMPLATE)?.editor;
  if (library.isLoading) {
    return (
      <p className="helm-muted mt-3 text-sm" role="status">
        Loading…
      </p>
    );
  }
  return spec ? <ConditionsForm {...props} spec={spec} /> : null;
};

const ConditionsForm: React.FC<EditorProps & { readonly spec: ConditionEditorSpec }> = ({
  params,
  onChange,
  disabled = false,
  spec,
}) => {
  const { orgSlug, access, role } = useHelm();
  const id = useId();
  const fieldOf = (key: string): ConditionField | undefined => spec.fields.find((f) => f.key === key);
  const stored = Array.isArray(params.conditions) ? (params.conditions as CustomCondition[]) : [];
  const nextId = useRef(stored.length);
  const [current, setRows] = useState<Row[]>(() =>
    stored.map((condition, index) => rowFrom(index, condition, spec.fields.find((f) => f.key === condition.field))),
  );
  const [name, setName] = useState(typeof params.name === "string" ? params.name : "");
  const locked = !access.custom;
  const off = disabled || locked;

  const send = (next: Row[]): void => onChange("conditions", next.map((row) => conditionOf(row, fieldOf(row.field))));
  const update = (row: Row, commit: boolean): void => {
    const next = current.map((r) => (r.id === row.id ? row : r));
    setRows(next);
    if (commit) {
      send(next);
    }
  };
  const add = (): void => {
    const first = spec.fields[0];
    if (!first) {
      return;
    }
    const next = [...current, freshRow(nextId.current++, first, current[0]?.currency ?? "INR")];
    setRows(next);
    send(next);
  };
  const remove = (row: Row): void => {
    const next = current.filter((r) => r.id !== row.id);
    setRows(next);
    send(next);
  };
  const commitName = (): void => {
    if (name.trim() !== params.name) {
      onChange("name", name.trim());
    }
  };
  const match = params.match === "any" ? "any" : "all";
  const outcome = typeof params.outcome === "string" ? params.outcome : "hold";

  return (
    <div className="helm-panel-raised mt-3 space-y-3 p-3" data-testid="helm-conditions-editor">
      {locked ? (
        <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="helm-upgrade-note">
          <Lock className="h-3.5 w-3.5" aria-hidden="true" />
          Custom conditions are part of the Enterprise plan.
          {role === "OWNER" || role === "BILLING" ? (
            <Link to={organizationBillingPath(orgSlug)} className="helm-accent hover:underline">
              See plans
            </Link>
          ) : (
            <span className="helm-muted">Ask your organization&apos;s owner about upgrading.</span>
          )}
        </p>
      ) : null}
      <label className="block text-xs" htmlFor={`${id}-name`}>
        <span className="helm-label block">Name</span>
      </label>
      <input
        id={`${id}-name`}
        className="helm-input"
        maxLength={80}
        value={name}
        placeholder="Large invoices from new suppliers"
        disabled={off}
        onChange={(event) => setName(event.target.value)}
        onBlur={commitName}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            commitName();
          }
        }}
      />
      <fieldset disabled={off}>
        <legend className="helm-label">When</legend>
        <div role="radiogroup" aria-label="When the rule applies" className="mt-1 flex flex-wrap gap-2">
          {spec.matches.map((option) => (
            <label key={option.value} className="helm-chip cursor-pointer gap-1.5 py-1">
              <input
                type="radio"
                name={`${id}-match`}
                value={option.value}
                checked={match === option.value}
                onChange={() => onChange("match", option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>
      <ol className="space-y-2" aria-label="Conditions">
        {current.map((row, index) => (
          <ConditionRow
            key={row.id}
            row={row}
            index={index}
            spec={spec}
            disabled={off}
            canRemove={current.length > 1}
            onChange={update}
            onCommit={() => send(current)}
            onRemove={() => remove(row)}
          />
        ))}
      </ol>
      <button type="button" className="helm-btn" disabled={off || current.length >= spec.max_conditions} onClick={add}>
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add a condition
      </button>
      {current.length >= spec.max_conditions ? (
        <p className="helm-muted text-xs">A custom condition has at most {spec.max_conditions} conditions.</p>
      ) : null}
      <fieldset disabled={off}>
        <legend className="helm-label">Then</legend>
        <div role="radiogroup" aria-label="What Helm does with a matching invoice" className="mt-1 flex flex-wrap gap-2">
          {spec.outcomes.map((option) => (
            <label key={option.value} className="helm-chip cursor-pointer gap-1.5 py-1">
              <input
                type="radio"
                name={`${id}-outcome`}
                value={option.value}
                checked={outcome === option.value}
                onChange={() => onChange("outcome", option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
        <p className="helm-muted mt-1 text-xs">{spec.outcomes.find((o) => o.value === outcome)?.help}</p>
      </fieldset>
    </div>
  );
};

export default ConditionsEditor;
