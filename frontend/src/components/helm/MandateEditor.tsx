import React, { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { History } from "lucide-react";

import { ConditionsEditor, CUSTOM_TEMPLATE } from "@/components/helm/ConditionsEditor";
import { EditableReadback, fromMicros, toMicros } from "@/components/helm/EditableReadback";
import { errorMessage } from "@/services/api/errors";
import { createHelmMandate, previewHelmMandate, updateHelmMandate } from "@/services/api/helm";
import type { HelmMandate, MandatePreview } from "@/types/helm";

/**
 * One mandate being shaped before it is saved: a drafted sentence, a "did you mean", a gallery
 * template whose value must be the person's own, or an existing mandate being edited (a new,
 * immutable version). Every change re-runs the server's validator; nothing is saved until the
 * person presses the button, and only when the validator has nothing left to say.
 *
 * New mandates start in Propose mode (brief §2). Editing an existing one also sets how Helm acts:
 * Watch (only report), Propose (a person approves each action) or Act (Helm runs an action alone
 * within the act cap; at 0, which is the default, every payment still asks). Owners and admins
 * only; the server checks it again.
 */

type Params = Record<string, unknown>;
type Mode = "watch" | "propose" | "act";

type Autonomy = {
  mode: Mode;
  act_cap: { amount_micros: number; currency: string } | null;
} & Record<string, unknown>;

export interface EditorStart {
  readonly template: string;
  readonly title: string;
  readonly preview: MandatePreview;
  /** Required parameters still showing an example (a drafted sentence did not state them). */
  readonly unstated?: readonly string[];
  /** Problems the person must resolve outside this editor (a supplier Helm has never seen). */
  readonly blocking?: readonly string[];
}

const MODES: { value: Mode; label: string; help: string }[] = [
  { value: "watch", label: "Watch", help: "Helm reports when the mandate drifts, and proposes nothing." },
  { value: "propose", label: "Propose", help: "Helm proposes each action; a person approves it." },
  { value: "act", label: "Act", help: "Helm runs actions on its own up to the act cap; anything above it asks." },
];

const autonomyOf = (spec: Record<string, unknown> | null | undefined): Autonomy => {
  const raw = (spec?.autonomy ?? {}) as Partial<Autonomy>;
  return { mode: (raw.mode as Mode) ?? "propose", act_cap: raw.act_cap ?? null };
};

const AutonomyFields: React.FC<{
  readonly name: string;
  readonly value: Autonomy;
  readonly currency: string;
  readonly onChange: (next: Autonomy) => void;
  readonly disabled: boolean;
}> = ({ name, value, currency, onChange, disabled }) => {
  const [cap, setCap] = useState(value.act_cap ? fromMicros(value.act_cap.amount_micros) : "0");
  const [capCurrency, setCapCurrency] = useState(value.act_cap?.currency ?? currency);
  const capProblem = toMicros(cap) === null ? "Write the cap as an amount, for example 25000 or 0." : null;

  const setMode = (mode: Mode): void =>
    onChange({ mode, act_cap: mode === "act" ? value.act_cap : null });

  return (
    <fieldset className="mt-3 space-y-2" disabled={disabled}>
      <legend className="helm-label">How Helm acts</legend>
      <div role="radiogroup" aria-label={`How Helm acts on ${name}`} className="flex flex-wrap gap-2">
        {MODES.map((mode) => (
          <label key={mode.value} className="helm-chip cursor-pointer gap-1.5 py-1">
            <input
              type="radio"
              name={`helm-mode-${name}`}
              value={mode.value}
              checked={value.mode === mode.value}
              onChange={() => setMode(mode.value)}
            />
            {mode.label}
          </label>
        ))}
      </div>
      <p className="helm-muted text-xs">{MODES.find((m) => m.value === value.mode)?.help}</p>
      {value.mode === "act" ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">
            <span className="helm-label block">Act cap</span>
            <input
              className="helm-input helm-figure mt-1 w-32"
              inputMode="decimal"
              value={cap}
              aria-describedby={`helm-cap-help-${name}`}
              onChange={(event) => {
                setCap(event.target.value);
                const micros = toMicros(event.target.value);
                if (micros !== null) {
                  onChange({ mode: "act", act_cap: { amount_micros: micros, currency: capCurrency.toUpperCase() } });
                }
              }}
            />
          </label>
          <label className="text-xs">
            <span className="helm-label block">Currency</span>
            <input
              className="helm-input helm-figure mt-1 w-16 uppercase"
              maxLength={3}
              value={capCurrency}
              onChange={(event) => {
                setCapCurrency(event.target.value);
                const micros = toMicros(cap);
                if (micros !== null && /^[A-Za-z]{3}$/.test(event.target.value)) {
                  onChange({ mode: "act", act_cap: { amount_micros: micros, currency: event.target.value.toUpperCase() } });
                }
              }}
            />
          </label>
          <p id={`helm-cap-help-${name}`} className="helm-muted basis-full text-xs">
            The largest single action Helm may run without asking. At 0, every payment still asks; holds and reminders
            run alone.
          </p>
          {capProblem ? (
            <p role="alert" className="basis-full text-xs text-red-600 dark:text-red-400">
              {capProblem}
            </p>
          ) : null}
        </div>
      ) : null}
    </fieldset>
  );
};

export const MandateEditor: React.FC<{
  readonly organizationId: string;
  readonly bookId: string;
  readonly start: EditorStart;
  /** Present: editing this mandate (a new version); absent: adding a new one in Propose mode. */
  readonly mandate?: HelmMandate;
  readonly onDone: (mandate: HelmMandate) => void;
  readonly onCancel: () => void;
  /** Offered for a new mandate: replay it, with the values as they stand now, in the Time machine. */
  readonly backtest?:
    | { readonly months: number; readonly pending: boolean; readonly run: (spec: { params: Params; scope: Record<string, unknown> }) => void }
    | undefined;
}> = ({ organizationId, bookId, start, mandate, onDone, onCancel, backtest }) => {
  const spec = start.preview.spec;
  const [params, setParams] = useState<Params>((spec?.params as Params) ?? {});
  const [scope] = useState<Record<string, unknown>>((spec?.scope as Record<string, unknown>) ?? {});
  const [autonomy, setAutonomy] = useState<Autonomy>(autonomyOf(mandate?.spec ?? spec));
  const [preview, setPreview] = useState<MandatePreview>(start.preview);
  const [unstated, setUnstated] = useState<string[]>([...(start.unstated ?? [])]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);

  const check = useMutation({
    mutationFn: (next: { params: Params; autonomy: Autonomy }) =>
      previewHelmMandate(organizationId, bookId, {
        template: start.template,
        params: next.params,
        scope,
        ...(mandate ? { autonomy: next.autonomy, mandate_id: mandate.id } : {}),
      }),
    onSuccess: (result) => setPreview(result),
    onError: (error) => toast.error(errorMessage(error, "Helm could not check this mandate.")),
  });

  const save = useMutation({
    mutationFn: () =>
      mandate
        ? updateHelmMandate(organizationId, bookId, mandate.id, { params, scope, autonomy })
        : createHelmMandate(organizationId, bookId, { template: start.template, params, scope, activate: true }),
    onSuccess: (saved) => {
      toast.success(
        mandate
          ? `${saved.title}: version ${saved.version} saved.`
          : `${saved.title} is active, in Propose mode: Helm suggests, a person decides.`,
      );
      onDone(saved);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not save this mandate.")),
  });

  // The server's preview speaks for the template's own example; the editor tracks which of them the
  // person has since set, so its message is shown once, in the editor's words.
  const serverProblems = (preview.problems ?? []).filter((p) => !p.startsWith("Set the "));
  const problems = [
    ...(start.blocking ?? []),
    ...unstated.map((name) => `Set the ${name.replace(/_/g, " ")}: the value shown is an example, not yours.`),
    ...serverProblems,
  ];
  const ready = problems.length === 0 && !check.isPending;
  // A custom condition (Enterprise) is built in a rows editor rather than by pressing chips.
  const custom = start.template === CUSTOM_TEMPLATE;
  const currency = String(((params.floor ?? params.threshold) as { currency?: string } | undefined)?.currency ?? "INR");

  const change = (param: string, value: unknown): void => {
    const next = { ...params, [param]: value };
    setParams(next);
    setUnstated((names) => names.filter((name) => name !== param));
    check.mutate({ params: next, autonomy });
  };

  return (
    <section className="helm-panel p-4" aria-labelledby={`helm-editor-${start.template}`} data-testid="helm-mandate-editor">
      <h3 id={`helm-editor-${start.template}`} ref={heading} tabIndex={-1} className="text-sm font-semibold outline-none">
        {mandate ? `Edit ${start.title}` : start.title}
      </h3>
      <p className="helm-muted mt-0.5 text-xs">
        {custom
          ? "Build the rule below; the sentence is how Helm reads it."
          : "Press a highlighted value to change it."}
      </p>
      <EditableReadback
        readback={preview.readback}
        params={params}
        onChange={change}
        disabled={check.isPending || save.isPending}
        className="mt-2 text-sm"
      />
      {custom ? <ConditionsEditor params={params} onChange={change} disabled={save.isPending} /> : null}
      {mandate ? (
        <AutonomyFields
          name={mandate.title}
          value={autonomy}
          currency={currency}
          disabled={save.isPending}
          onChange={(next) => {
            setAutonomy(next);
            check.mutate({ params, autonomy: next });
          }}
        />
      ) : (
        <p className="helm-muted mt-2 text-xs">It starts in Propose mode: Helm suggests, a person decides.</p>
      )}
      {problems.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm text-amber-700 dark:text-amber-300" aria-live="polite" data-testid="helm-problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="helm-btn helm-btn-primary"
          disabled={!ready || save.isPending}
          onClick={() => save.mutate()}
        >
          {mandate ? `Save as version ${mandate.version + 1}` : "Add in Propose mode"}
        </button>
        {backtest && !mandate ? (
          <button
            type="button"
            className="helm-btn"
            disabled={!ready || backtest.pending}
            onClick={() => backtest.run({ params, scope })}
          >
            <History className="h-3.5 w-3.5" aria-hidden="true" />
            See what it would have done over the last {backtest.months} months
          </button>
        ) : null}
        <button type="button" className="helm-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
};

export default MandateEditor;
