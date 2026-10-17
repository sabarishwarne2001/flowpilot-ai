import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Plus } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { toMicros } from "@/components/helm/EditableReadback";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { LoadFailed } from "@/components/common/LoadFailed";
import { BacktestReport } from "@/pages/helm/HelmTimeMachine";
import { helmPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import {
  addHelmCashPosition,
  createHelmMandate,
  getHelmBacktest,
  getHelmLibrary,
  getHelmSettings,
  helmKeys,
  requestHelmBacktest,
  updateHelmSettings,
} from "@/services/api/helm";
import type { HelmBacktest, HelmSettings, LibraryTemplate } from "@/types/helm";

/**
 * Helm's first run (brief §10.3), four steps, resumable (the step is kept on the book):
 *   1. who we are: the legal names and tax ids Helm tells our sales from our purchases by,
 *      pre-filled from the organization and the invoices;
 *   2. our cash: typed by a person (Helm never guesses it), or left for later;
 *   3. mandates recommended for the kind of business, in plain words; those whose value has no
 *      default (a cash floor, a PO threshold) are added only with the person's own number;
 *   4. the backtest runs by itself and shows what Helm would have held.
 * Helm then starts in Propose mode.
 */

const STEPS = ["Who you are", "Your cash", "Your mandates", "What Helm would have held"] as const;

const today = (): string => new Date().toISOString().slice(0, 10);

/**
 * The first run as a boot sequence: a progress rail that fills as steps finish, and a glowing
 * marker per step (done, current, to come). Each step also says its state in words for screen
 * readers; the rail is decoration.
 */
const Stepper: React.FC<{ readonly step: number }> = ({ step }) => (
  <div className="helm-panel helm-enter p-4">
    <div className="helm-rail-track" aria-hidden="true">
      <div className="helm-rail-fill" style={{ transform: `scaleX(${Math.min(1, (step + 0.5) / STEPS.length)})` }} />
    </div>
    <ol className="mt-4 grid gap-3 text-xs sm:grid-cols-4" aria-label="Setting up Helm">
      {STEPS.map((name, index) => {
        const state = index < step ? "done" : index === step ? "current" : "next";
        return (
          <li key={name} className="flex items-center gap-2.5" aria-current={index === step ? "step" : undefined}>
            <span className="helm-step-marker" data-state={state} aria-hidden="true">
              {index < step ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            <span className={index === step ? "font-semibold" : "helm-muted"}>
              {name}
              <span className="sr-only">{state === "done" ? " (done)" : state === "current" ? " (now)" : ""}</span>
            </span>
          </li>
        );
      })}
    </ol>
  </div>
);

const Identities: React.FC<{ readonly settings: HelmSettings; readonly onDone: () => void }> = ({ settings, onDone }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const [rows, setRows] = useState(
    (settings.identities.length > 0 ? settings.identities : [{ name: "", tax_id: "" }]).map((i) => ({
      name: i.name ?? "",
      tax_id: i.tax_id ?? "",
    })),
  );
  const save = useMutation({
    mutationFn: () =>
      updateHelmSettings(organizationId, book.id, {
        identities: rows.map((r) => ({ name: r.name.trim() || null, tax_id: r.tax_id.trim() || null })),
        first_run_step: 1,
      }),
    onSuccess: onDone,
    onError: (error) => toast.error(errorMessage(error, "Helm could not save who you are.")),
  });
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm">
        Helm tells your sales from your purchases by the names and tax ids on each invoice. Check the ones it found: an
        invoice billed to one of these is one you pay.
      </p>
      {rows.map((row, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs">
            <span className="helm-label block">Legal name {index + 1}</span>
            <input
              className="helm-input mt-1 w-full"
              value={row.name}
              onChange={(event) => setRows(rows.map((r, i) => (i === index ? { ...r, name: event.target.value } : r)))}
            />
          </label>
          <label className="text-xs">
            <span className="helm-label block">GSTIN or tax id {index + 1}</span>
            <input
              className="helm-input helm-figure mt-1 w-full uppercase"
              value={row.tax_id}
              onChange={(event) => setRows(rows.map((r, i) => (i === index ? { ...r, tax_id: event.target.value } : r)))}
            />
          </label>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        {rows.length < 5 ? (
          <button type="button" className="helm-btn" onClick={() => setRows([...rows, { name: "", tax_id: "" }])}>
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Another name
          </button>
        ) : null}
        <button
          type="submit"
          className="helm-btn helm-btn-primary"
          disabled={save.isPending || rows.every((r) => !r.name.trim() && !r.tax_id.trim())}
        >
          These are us
        </button>
      </div>
    </form>
  );
};

const Cash: React.FC<{ readonly settings: HelmSettings; readonly onDone: () => void }> = ({ settings, onDone }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const [currency, setCurrency] = useState(settings.cash[0]?.currency ?? "INR");
  const [amount, setAmount] = useState("");
  const [asOf, setAsOf] = useState(today());
  const next = useMutation({
    mutationFn: async (skip: boolean) => {
      if (!skip) {
        await addHelmCashPosition(organizationId, book.id, { currency: currency.trim().toUpperCase(), amount: amount.replace(/[,\s]/g, ""), as_of: asOf });
      }
      return updateHelmSettings(organizationId, book.id, { first_run_step: 2 });
    },
    onSuccess: onDone,
    onError: (error) => toast.error(errorMessage(error, "Helm could not save the cash position.")),
  });
  const valid = toMicros(amount) !== null && /^[A-Za-z]{3}$/.test(currency.trim());
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) {
          next.mutate(false);
        }
      }}
    >
      <p className="text-sm">
        How much is in the bank today? Helm plans payments around it and never guesses it. You can add statements later;
        Helm reads their closing balances.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">
          <span className="helm-label block">Currency</span>
          <input className="helm-input helm-figure mt-1 w-20 uppercase" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="helm-label block">Amount</span>
          <input className="helm-input helm-figure mt-1 w-40" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="helm-label block">As of</span>
          <input type="date" className="helm-input mt-1" value={asOf} max={today()} onChange={(e) => setAsOf(e.target.value)} />
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="helm-btn helm-btn-primary" disabled={!valid || next.isPending}>
          Save the cash
        </button>
        <button type="button" className="helm-btn" disabled={next.isPending} onClick={() => next.mutate(true)}>
          Add it later
        </button>
      </div>
      <p className="helm-muted text-xs">Without it, Helm still keeps every rule, but shows your cash as unknown.</p>
    </form>
  );
};

const Mandates: React.FC<{ readonly settings: HelmSettings; readonly onDone: () => void }> = ({ settings, onDone }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const library = useQuery({ queryKey: helmKeys.library(organizationId), queryFn: () => getHelmLibrary(organizationId) });
  const [type, setType] = useState(settings.business_type ?? "");
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [values, setValues] = useState<Record<string, { amount: string; currency: string }>>({});
  const templates = useMemo(() => new Map((library.data?.templates ?? []).map((t) => [t.key, t])), [library.data]);
  const recommended: LibraryTemplate[] = (library.data?.business_types.find((b) => b.key === type)?.recommended ?? [])
    .map((key) => templates.get(key))
    .filter((t): t is LibraryTemplate => Boolean(t));
  const isOn = (t: LibraryTemplate): boolean =>
    t.required.length > 0 ? chosen[t.key] === true && toMicros(values[t.key]?.amount ?? "") !== null : chosen[t.key] !== false;

  const add = useMutation({
    mutationFn: async () => {
      const failed: string[] = [];
      for (const t of recommended.filter(isOn)) {
        const value = values[t.key];
        const params =
          t.required.length > 0 && value
            ? { [t.required[0] as string]: { amount_micros: toMicros(value.amount), currency: value.currency.trim().toUpperCase() || "INR" } }
            : undefined;
        try {
          await createHelmMandate(organizationId, book.id, { template: t.key, activate: true, ...(params ? { params } : {}) });
        } catch (error) {
          failed.push(`${t.title}: ${errorMessage(error, "not added")}`);
        }
      }
      await updateHelmSettings(organizationId, book.id, { business_type: type, first_run_step: 3 });
      return failed;
    },
    onSuccess: (failed) => {
      failed.forEach((message) => toast.error(message));
      onDone();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not add the mandates.")),
  });

  if (library.isError) {
    return <LoadFailed what="the mandates" onRetry={() => void library.refetch()} />;
  }
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <fieldset>
        <legend className="text-sm">What kind of business is this?</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {(library.data?.business_types ?? []).map((b) => (
            <label key={b.key} className="helm-panel-raised flex cursor-pointer items-center gap-2 p-2.5 text-sm">
              <input type="radio" name="helm-business-type" value={b.key} checked={type === b.key} onChange={() => setType(b.key)} />
              {b.label}
            </label>
          ))}
        </div>
      </fieldset>
      {recommended.length > 0 ? (
        <fieldset>
          <legend className="text-sm">Recommended for you. Each starts in Propose mode: Helm suggests, a person decides.</legend>
          <ul className="mt-2 space-y-2">
            {recommended.map((t) => (
              <li key={t.key} className="helm-panel-raised p-2.5">
                <label className="flex cursor-pointer items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={isOn(t)}
                    onChange={(event) => setChosen({ ...chosen, [t.key]: event.target.checked })}
                  />
                  <span>
                    <span className="font-medium">{t.title}</span>
                    <span className="helm-muted block text-xs">{t.summary}</span>
                  </span>
                </label>
                {t.required.length > 0 ? (
                  <div className="ml-6 mt-2 flex flex-wrap items-end gap-2">
                    <label className="text-xs">
                      <span className="helm-label block">Your {t.required[0]?.replace(/_/g, " ")}</span>
                      <input
                        className="helm-input helm-figure mt-1 w-36"
                        inputMode="decimal"
                        value={values[t.key]?.amount ?? ""}
                        onChange={(event) => {
                          setValues({ ...values, [t.key]: { currency: values[t.key]?.currency ?? "INR", amount: event.target.value } });
                          setChosen({ ...chosen, [t.key]: event.target.value.trim() !== "" });
                        }}
                      />
                    </label>
                    <label className="text-xs">
                      <span className="helm-label block">Currency</span>
                      <input
                        className="helm-input helm-figure mt-1 w-16 uppercase"
                        maxLength={3}
                        value={values[t.key]?.currency ?? "INR"}
                        onChange={(event) =>
                          setValues({ ...values, [t.key]: { amount: values[t.key]?.amount ?? "", currency: event.target.value } })
                        }
                      />
                    </label>
                    <span className="helm-muted basis-full text-xs">Added only with your own number.</span>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </fieldset>
      ) : null}
      <button type="submit" className="helm-btn helm-btn-primary" disabled={!type || add.isPending}>
        {recommended.some(isOn) ? "Add these in Propose mode" : "Continue without mandates"}
      </button>
    </form>
  );
};

const Replay: React.FC<{ readonly onDone: () => void }> = ({ onDone }) => {
  const { organizationId, overview } = useHelm();
  const book = useHelmBook();
  const [backtest, setBacktest] = useState<string | null>(null);
  const started = useRef(false);
  const months = Math.max(1, Math.min(6, overview.limits.backtest_months));
  const start = useMutation({
    mutationFn: () => requestHelmBacktest(organizationId, { book_id: book.id, months }),
    onSuccess: (queued) => setBacktest(queued.id),
    onError: (error) => toast.error(errorMessage(error, "Helm could not start the backtest.")),
  });
  useEffect(() => {
    if (!started.current) {
      started.current = true;
      start.mutate();
    }
  }, [start]);
  const row = useQuery({
    queryKey: helmKeys.backtest(organizationId, backtest ?? "none"),
    queryFn: () => getHelmBacktest(organizationId, backtest as string),
    enabled: backtest !== null,
    refetchInterval: (query) => (["QUEUED", "RUNNING"].includes(query.state.data?.status ?? "QUEUED") ? 2_000 : false),
  });
  const done: HelmBacktest | undefined = row.data?.status === "DONE" ? row.data : undefined;
  const workspaceSlug = overview.workspaces.find((w) => w.id === book.workspace_id)?.slug ?? null;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        Helm is replaying the last {months} months of your own documents with the mandates you chose. Nothing is
        executed.
      </p>
      {done ? (
        <BacktestReport row={done} workspaceSlug={workspaceSlug} />
      ) : row.data?.status === "FAILED" ? (
        <p role="alert" className="text-sm">
          The replay could not finish ({row.data.error ?? "an unexpected error"}). You can run it again from the Time
          machine.
        </p>
      ) : (
        <p role="status" aria-live="polite" className="helm-muted text-sm">
          Replaying…
        </p>
      )}
      <button type="button" className="helm-btn helm-btn-primary" onClick={onDone} disabled={!done && row.data?.status !== "FAILED" && !start.isError}>
        Open the cockpit
      </button>
    </div>
  );
};

export const HelmFirstRun: React.FC = () => {
  const { organizationId, orgSlug } = useHelm();
  const book = useHelmBook();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: helmKeys.settings(organizationId, book.id), queryFn: () => getHelmSettings(organizationId, book.id) });
  const [step, setStep] = useState<number | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (step === null && settings.data) {
      setStep(Math.min(3, settings.data.first_run_step ?? 0));
    }
  }, [step, settings.data]);
  useEffect(() => heading.current?.focus(), [step]);

  const advance = (): void => {
    void queryClient.invalidateQueries({ queryKey: helmKeys.settings(organizationId, book.id) });
    void queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
    setStep((s) => (s ?? 0) + 1);
  };
  const finish = useMutation({
    mutationFn: () => updateHelmSettings(organizationId, book.id, { first_run_step: 4 }),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
      navigate(`${helmPath(orgSlug)}?book=${encodeURIComponent(book.id)}`);
    },
  });

  if (settings.isError) {
    return <LoadFailed what="Helm's setup" onRetry={() => void settings.refetch()} />;
  }
  if (!settings.data || step === null) {
    return <HelmSkeleton label="Loading…" />;
  }
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <HelmPageHeader
        eyebrow={`Step ${step + 1} of ${STEPS.length}`}
        title={`Set up Helm on ${book.name}`}
        description="Four short steps. Helm starts in Propose mode: it suggests, a person decides."
      />
      <Stepper step={step} />
      <section key={step} className="helm-panel helm-panel-strong helm-enter p-6" aria-labelledby="helm-setup-step">
        <h2 id="helm-setup-step" ref={heading} tabIndex={-1} className="mb-4 text-lg font-semibold tracking-tight outline-none">
          {STEPS[step]}
        </h2>
        {step === 0 ? <Identities settings={settings.data} onDone={advance} /> : null}
        {step === 1 ? <Cash settings={settings.data} onDone={advance} /> : null}
        {step === 2 ? <Mandates settings={settings.data} onDone={advance} /> : null}
        {step === 3 ? <Replay onDone={() => finish.mutate()} /> : null}
      </section>
    </div>
  );
};

export default HelmFirstRun;
