import React, { useId, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, FlaskConical, X } from "lucide-react";

import { toMicros } from "@/components/helm/EditableReadback";
import { Money, StateBadge, percent } from "@/components/helm/HelmBits";
import { errorMessage } from "@/services/api/errors";
import { runHelmWhatIf } from "@/services/api/helm";
import { formatCalendarDate, formatNumber } from "@/utils/displayTime";
import type { HelmWhatIf, HelmWhatIfInput } from "@/types/helm";

export interface Holdable {
  readonly promiseId: string;
  readonly label: string;
}

interface WhatIfPanelProps {
  readonly organizationId: string;
  readonly bookId: string;
  /** The currencies the book holds cash in (a cash change needs one). */
  readonly currencies: readonly string[];
  /** Payments in today's plan that can be held in the what-if. */
  readonly holdable: readonly Holdable[];
  readonly result: HelmWhatIf | null;
  readonly onResult: (result: HelmWhatIf | null) => void;
}

type Direction = "none" | "less" | "more";

const MAX_DELAY_DAYS = 90;
const MAX_HOLDS = 50;

/**
 * "What if…" (beside the flight path): a change to today's cash, customers paying later, or
 * invoices held, compared with today's plan. Helm re-simulates both with the same futures and
 * saves nothing; the cockpit draws the what-if as a second, dashed line.
 */
export const WhatIfPanel: React.FC<WhatIfPanelProps> = ({ organizationId, bookId, currencies, holdable, result, onResult }) => {
  const [direction, setDirection] = useState<Direction>("none");
  const [amount, setAmount] = useState("");
  const [chosenCurrency, setChosenCurrency] = useState<string | null>(null);
  const [delay, setDelay] = useState("");
  const [holds, setHolds] = useState<readonly string[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const formId = useId();

  const currency = chosenCurrency && currencies.includes(chosenCurrency) ? chosenCurrency : currencies[0] ?? null;
  const micros = direction === "none" ? null : toMicros(amount);
  const cashProblem =
    direction === "none"
      ? null
      : micros === null || micros === 0
        ? "Write the amount, for example 200000."
        : micros > Number.MAX_SAFE_INTEGER
          ? "That amount is too large for a what-if."
          : null;
  const delayText = delay.trim();
  const delayDays = delayText === "" ? 0 : Number(delayText);
  const delayProblem =
    delayText !== "" && (!/^\d{1,2}$/.test(delayText) || delayDays > MAX_DELAY_DAYS)
      ? `Customers can pay 0 to ${formatNumber(MAX_DELAY_DAYS)} days later.`
      : null;
  const cashChange = direction === "none" || micros === null || cashProblem ? 0 : direction === "less" ? -micros : micros;
  const nothing = cashChange === 0 && delayDays === 0 && holds.length === 0;
  const blocked = Boolean(cashProblem || delayProblem) || nothing;

  const run = useMutation({
    mutationFn: (body: HelmWhatIfInput) => runHelmWhatIf(organizationId, bookId, body),
    onMutate: () => setFailure(null),
    onSuccess: (data) => onResult(data),
    onError: (error) => setFailure(errorMessage(error, "Helm could not run the what-if.")),
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (blocked || run.isPending) {
      return;
    }
    const body: HelmWhatIfInput = {};
    if (cashChange !== 0) {
      body.cash_change_micros = cashChange;
      if (currency) {
        body.currency = currency;
      }
    }
    if (delayDays > 0) {
      body.delay_receivables_days = delayDays;
    }
    if (holds.length > 0) {
      body.hold_promise_ids = [...holds];
    }
    run.mutate(body);
  };

  const toggleHold = (promiseId: string) =>
    setHolds((current) => (current.includes(promiseId) ? current.filter((id) => id !== promiseId) : [...current, promiseId]));

  const clear = () => {
    setDirection("none");
    setAmount("");
    setDelay("");
    setHolds([]);
    setFailure(null);
    onResult(null);
  };

  return (
    <section aria-labelledby={`${formId}-title`} className="helm-panel p-4" data-testid="helm-what-if">
      <h2 id={`${formId}-title`} className="helm-label flex items-center gap-1.5">
        <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
        What if…
      </h2>
      <p className="helm-muted mt-1 text-xs">
        Try a change and Helm compares it with today&apos;s plan. Nothing is saved or sent.
      </p>
      <form className="mt-3 space-y-3 text-sm" onSubmit={submit} noValidate>
        <fieldset className="space-y-1.5">
          <legend className="text-xs font-semibold">Cash today</legend>
          <div className="flex flex-wrap gap-2">
            <select
              className="helm-input w-auto min-w-[8.5rem] flex-1"
              aria-label="Change in cash today"
              value={direction}
              onChange={(event) => setDirection(event.target.value as Direction)}
              disabled={currencies.length === 0}
            >
              <option value="none">No change</option>
              <option value="less">Less cash by</option>
              <option value="more">More cash by</option>
            </select>
            {currencies.length > 1 ? (
              <select
                className="helm-input w-20 helm-figure"
                aria-label="Currency of the cash change"
                value={currency ?? ""}
                onChange={(event) => setChosenCurrency(event.target.value)}
                disabled={direction === "none"}
              >
                {currencies.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          {direction !== "none" ? (
            <label className="block">
              <span className="sr-only">Amount{currency ? ` in ${currency}` : ""}</span>
              <span className="flex items-center gap-2">
                <input
                  className="helm-input helm-figure"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="200000"
                  value={amount}
                  aria-invalid={cashProblem ? true : undefined}
                  aria-describedby={cashProblem ? `${formId}-cash` : undefined}
                  onChange={(event) => setAmount(event.target.value)}
                />
                {currency && currencies.length === 1 ? <span className="helm-figure helm-muted text-xs">{currency}</span> : null}
              </span>
            </label>
          ) : null}
          {currencies.length === 0 ? (
            <p className="helm-muted text-xs">Helm does not know this book&apos;s cash yet. Type a cash position in Settings to change it here.</p>
          ) : null}
          {cashProblem && amount.trim() !== "" ? (
            <p id={`${formId}-cash`} className="helm-state-breached text-xs">
              {cashProblem}
            </p>
          ) : null}
        </fieldset>

        <label className="block space-y-1.5">
          <span className="block text-xs font-semibold">Customers pay later by</span>
          <span className="flex items-center gap-2">
            <input
              className="helm-input helm-figure w-24"
              inputMode="numeric"
              autoComplete="off"
              placeholder="0"
              value={delay}
              aria-invalid={delayProblem ? true : undefined}
              aria-describedby={delayProblem ? `${formId}-delay` : undefined}
              onChange={(event) => setDelay(event.target.value)}
            />
            <span className="helm-muted text-xs">days than usual</span>
          </span>
          {delayProblem ? (
            <span id={`${formId}-delay`} className="helm-state-breached block text-xs">
              {delayProblem}
            </span>
          ) : null}
        </label>

        {holdable.length > 0 ? (
          <details className="text-xs">
            <summary className="helm-accent cursor-pointer font-semibold">
              Hold payments{holds.length > 0 ? ` (${formatNumber(holds.length)})` : ""}
            </summary>
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto pr-1">
              {holdable.slice(0, MAX_HOLDS).map((item) => (
                <li key={item.promiseId}>
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={holds.includes(item.promiseId)}
                      onChange={() => toggleHold(item.promiseId)}
                    />
                    <span className="min-w-0 break-words">{item.label}</span>
                  </label>
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="helm-btn helm-btn-primary" disabled={blocked || run.isPending}>
            {run.isPending ? "Comparing…" : "Run"}
          </button>
          {result ? (
            <button type="button" className="helm-btn" onClick={clear}>
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Clear
            </button>
          ) : null}
        </div>
        {nothing && !result ? (
          <p className="helm-muted text-xs">Change the cash, how late customers pay, or pick payments to hold.</p>
        ) : null}
        {failure ? (
          <p role="alert" className="helm-state-breached text-xs">
            {failure}
          </p>
        ) : null}
      </form>

      <p className="sr-only" aria-live="polite">
        {result ? result.summary : ""}
      </p>
      {result ? <WhatIfResult result={result} /> : null}
    </section>
  );
};

const WhatIfResult: React.FC<{ readonly result: HelmWhatIf }> = ({ result }) => {
  const { baseline, scenario, floors } = result;
  const currencies = [...new Set([...Object.keys(baseline.cash_today), ...Object.keys(scenario.cash_today)])].sort();
  return (
    <div className="helm-hairline mt-4 space-y-3 border-t pt-3" data-testid="helm-what-if-result">
      <p className="text-sm">{result.summary}</p>
      <div className="overflow-x-auto">
        <table className="helm-what-if-table w-full text-left text-xs">
          <caption className="sr-only">Today&apos;s plan and the what-if, side by side</caption>
          <thead>
            <tr className="helm-label">
              <th scope="col">
                <span className="sr-only">Figure</span>
              </th>
              <th scope="col">As things stand</th>
              <th scope="col">What if</th>
            </tr>
          </thead>
          <tbody>
            {currencies.map((code) => {
              const floor = floors[code];
              const before = baseline.floor_probability[code];
              const after = scenario.floor_probability[code];
              const lowBefore = baseline.lowest[code];
              const lowAfter = scenario.lowest[code];
              return (
                <React.Fragment key={code}>
                  <tr className="helm-hairline border-t">
                    <th scope="row" className="font-normal">
                      Cash today <span className="helm-figure">{code}</span>
                    </th>
                    <td>
                      <Money micros={baseline.cash_today[code]} currency={code} />
                    </td>
                    <td>
                      <Money micros={scenario.cash_today[code]} currency={code} />
                    </td>
                  </tr>
                  {floor !== undefined && before !== undefined && after !== undefined ? (
                    <tr className="helm-hairline border-t">
                      <th scope="row" className="font-normal">
                        Stays above the <Money micros={floor} currency={code} /> floor
                      </th>
                      <td className="helm-figure">{percent(before)}</td>
                      <td className="helm-figure">{percent(after)}</td>
                    </tr>
                  ) : null}
                  {lowBefore && lowAfter ? (
                    <tr className="helm-hairline border-t">
                      <th scope="row" className="font-normal">
                        Lowest cautious point
                      </th>
                      <td>
                        <Money micros={lowBefore.p10} currency={code} />
                        <span className="helm-muted block font-normal">{formatCalendarDate(lowBefore.on)}</span>
                      </td>
                      <td>
                        <Money micros={lowAfter.p10} currency={code} />
                        <span className="helm-muted block font-normal">{formatCalendarDate(lowAfter.on)}</span>
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
            <tr className="helm-hairline border-t">
              <th scope="row" className="font-normal">
                Payments in the plan
              </th>
              <td className="helm-figure">{formatNumber(baseline.payments)}</td>
              <td className="helm-figure">{formatNumber(scenario.payments)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {result.mandates.length > 0 ? (
        <div>
          <h3 className="helm-label">Mandates that change</h3>
          <ul className="mt-1.5 space-y-2 text-xs">
            {result.mandates.map((change) => (
              <li key={change.mandate_id}>
                <p className="font-semibold">{change.title}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  <StateBadge state={change.baseline.state} />
                  <ArrowRight className="helm-muted h-3 w-3" aria-hidden="true" />
                  <span className="sr-only">becomes</span>
                  <StateBadge state={change.scenario.state} />
                </p>
                {result.readable ? <p className="helm-muted mt-0.5">{change.scenario.detail}</p> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {scenario.conflict && result.readable && scenario.conflict.options.length > 0 ? (
        <div>
          <h3 className="helm-label">The what-if&apos;s options</h3>
          <ul className="helm-muted mt-1 list-disc space-y-1 pl-5 text-xs">
            {scenario.conflict.options.map((option) => (
              <li key={option}>{option}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="helm-muted text-[11px]">
        {result.note} Both are re-simulated now over {formatNumber(result.horizon_days)} days with the same{" "}
        {formatNumber(result.futures)} futures.
      </p>
    </div>
  );
};

export default WhatIfPanel;
