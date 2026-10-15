import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money } from "@/components/helm/HelmBits";
import { LoadFailed } from "@/components/common/LoadFailed";
import { errorMessage } from "@/services/api/errors";
import {
  addHelmCashPosition,
  getHelmSettings,
  helmKeys,
  listHelmCounterparties,
  updateHelmCounterparty,
  updateHelmSettings,
} from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { HelmCounterparty, HelmSettings as Settings } from "@/types/helm";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const today = (): string => new Date().toISOString().slice(0, 10);

interface IdentityDraft {
  name: string;
  tax_id: string;
}

const Identities: React.FC<{ readonly settings: Settings; readonly writable: boolean }> = ({ settings, writable }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<IdentityDraft[]>([]);
  useEffect(() => {
    setRows(
      settings.identities.length > 0
        ? settings.identities.map((i) => ({ name: i.name ?? "", tax_id: i.tax_id ?? "" }))
        : [{ name: "", tax_id: "" }],
    );
  }, [settings.identities]);

  const save = useMutation({
    mutationFn: () =>
      updateHelmSettings(organizationId, book.id, {
        identities: rows.map((r) => ({ name: r.name.trim() || null, tax_id: r.tax_id.trim() || null })),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(helmKeys.settings(organizationId, book.id), data);
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
      toast.success("Saved. Helm is re-reading which documents are sales and which are purchases.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not save the identities.")),
  });

  const update = (index: number, field: keyof IdentityDraft, value: string): void =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, [field]: value } : row)));

  return (
    <section aria-labelledby="helm-identities" className="helm-panel p-4">
      <h2 id="helm-identities" className="text-sm font-semibold">
        Who we are
      </h2>
      <p className="helm-muted mt-1 text-xs">
        Our legal names and tax ids (GSTIN). Helm uses them to tell an invoice we sent from one we received.
        {settings.identities_confirmed_at
          ? ` Confirmed ${formatCalendarDate(settings.identities_confirmed_at.slice(0, 10))}.`
          : " Suggested from your documents; please confirm."}
      </p>
      <form
        className="mt-3 space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        {rows.map((row, index) => (
          <div key={index} className="grid gap-2 sm:grid-cols-[1fr_14rem_auto]">
            <label className="text-xs">
              <span className="sr-only">Legal name {index + 1}</span>
              <input
                className="helm-input"
                value={row.name}
                placeholder="Legal name"
                onChange={(event) => update(index, "name", event.target.value)}
                disabled={!writable}
                maxLength={300}
              />
            </label>
            <label className="text-xs">
              <span className="sr-only">Tax id {index + 1}</span>
              <input
                className="helm-input helm-figure uppercase"
                value={row.tax_id}
                placeholder="GSTIN or tax id"
                onChange={(event) => update(index, "tax_id", event.target.value)}
                disabled={!writable}
                maxLength={64}
              />
            </label>
            {writable && rows.length > 1 ? (
              <button
                type="button"
                className="helm-btn"
                onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
                aria-label={`Remove identity ${index + 1}`}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : null}
          </div>
        ))}
        {writable ? (
          <div className="flex flex-wrap gap-2 pt-1">
            <button type="button" className="helm-btn" onClick={() => setRows((current) => [...current, { name: "", tax_id: "" }])}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add another
            </button>
            <button type="submit" className="helm-btn helm-btn-primary" disabled={save.isPending}>
              {settings.identities_confirmed_at ? "Save" : "Confirm"}
            </button>
          </div>
        ) : null}
      </form>
    </section>
  );
};

const Cash: React.FC<{ readonly settings: Settings; readonly canEnter: boolean }> = ({ settings, canEnter }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const [currency, setCurrency] = useState(settings.cash[0]?.currency ?? "INR");
  const [amount, setAmount] = useState("");
  const [asOf, setAsOf] = useState(today());
  const add = useMutation({
    mutationFn: () => addHelmCashPosition(organizationId, book.id, { currency, amount: amount.replace(/[, ]/g, ""), as_of: asOf }),
    onSuccess: () => {
      setAmount("");
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
      toast.success("Cash position saved. Helm is re-planning with it.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not save the cash position.")),
  });
  const valid = /^-?\d+(\.\d{1,2})?$/.test(amount.replace(/[, ]/g, "")) && /^[A-Za-z]{3}$/.test(currency);

  return (
    <section aria-labelledby="helm-cash" className="helm-panel p-4">
      <h2 id="helm-cash" className="text-sm font-semibold">
        Cash
      </h2>
      <p className="helm-muted mt-1 text-xs">
        What is in the bank, per currency. Helm reads it from your latest bank statement when it has one; a position you
        type here wins when it is newer. Helm never converts between currencies.
      </p>
      {settings.cash.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm">
          {settings.cash.map((position) => (
            <li key={position.currency} className="flex flex-wrap items-baseline gap-x-3">
              <Money micros={position.amount_micros} currency={position.currency} className="font-semibold" />
              <span className="helm-muted text-xs">
                on {formatCalendarDate(position.as_of)} ·{" "}
                {position.source === "STATEMENT" ? "from a bank statement" : "typed by a person"}
                {position.age_days > 7 ? ` · ${position.age_days} days old` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm">No cash position yet. Without one Helm cannot draw the flight path or keep a floor.</p>
      )}
      {canEnter ? (
        <form
          className="mt-3 grid gap-2 sm:grid-cols-[6rem_1fr_11rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) {
              add.mutate();
            }
          }}
        >
          <label className="text-xs">
            <span className="helm-label">Currency</span>
            <input className="helm-input helm-figure uppercase" value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
          </label>
          <label className="text-xs">
            <span className="helm-label">Amount</span>
            <input className="helm-input helm-figure" inputMode="decimal" value={amount} placeholder="2450000" onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label className="text-xs">
            <span className="helm-label">As of</span>
            <input className="helm-input" type="date" value={asOf} max={today()} onChange={(e) => setAsOf(e.target.value)} />
          </label>
          <div className="flex items-end">
            <button type="submit" className="helm-btn helm-btn-primary w-full" disabled={!valid || add.isPending}>
              Save
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
};

const Planning: React.FC<{ readonly settings: Settings; readonly writable: boolean }> = ({ settings, writable }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const [days, setDays] = useState<number[]>(settings.run_weekdays);
  const [cost, setCost] = useState(String(settings.cost_of_capital_pct));
  const [briefing, setBriefing] = useState(settings.briefing_enabled);
  useEffect(() => {
    setDays(settings.run_weekdays);
    setCost(String(settings.cost_of_capital_pct));
    setBriefing(settings.briefing_enabled);
  }, [settings]);
  const save = useMutation({
    mutationFn: () =>
      updateHelmSettings(organizationId, book.id, {
        run_weekdays: days,
        cost_of_capital_pct: Number(cost),
        briefing_enabled: briefing,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(helmKeys.settings(organizationId, book.id), data);
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
      toast.success("Saved.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not save these settings.")),
  });
  return (
    <section aria-labelledby="helm-planning" className="helm-panel p-4">
      <h2 id="helm-planning" className="text-sm font-semibold">
        Payment runs and planning
      </h2>
      <form
        className="mt-3 space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset>
          <legend className="helm-label">Days you pay suppliers</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {WEEKDAYS.map((name, index) => {
              const day = index + 1;
              return (
                <label key={name} className="helm-chip cursor-pointer">
                  <input
                    type="checkbox"
                    className="mr-1"
                    checked={days.includes(day)}
                    disabled={!writable}
                    onChange={(event) =>
                      setDays((current) => (event.target.checked ? [...current, day].sort((a, b) => a - b) : current.filter((d) => d !== day)))
                    }
                  />
                  {name}
                </label>
              );
            })}
          </div>
        </fieldset>
        <label className="block max-w-xs text-xs">
          <span className="helm-label">Cost of money, % a year</span>
          <input
            className="helm-input helm-figure mt-1"
            inputMode="decimal"
            value={cost}
            disabled={!writable}
            onChange={(event) => setCost(event.target.value)}
          />
          <span className="helm-muted mt-1 block">What paying early costs you; Helm weighs it against discounts.</span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={briefing} disabled={!writable} onChange={(event) => setBriefing(event.target.checked)} />
          Send owners and admins the Monday briefing email
        </label>
        {writable ? (
          <button type="submit" className="helm-btn helm-btn-primary" disabled={save.isPending || days.length === 0}>
            Save
          </button>
        ) : null}
      </form>
    </section>
  );
};

const MSME_CHOICES: { value: string; label: string }[] = [
  { value: "", label: "Not confirmed" },
  { value: "MICRO", label: "Micro" },
  { value: "SMALL", label: "Small" },
  { value: "MEDIUM", label: "Medium" },
  { value: "NONE", label: "Not an MSME" },
];

const CounterpartyRow: React.FC<{ readonly row: HelmCounterparty; readonly writable: boolean }> = ({ row, writable }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (msme: string) =>
      updateHelmCounterparty(organizationId, book.id, row.id, { msme_category: msme || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: helmKeys.counterparties(organizationId, book.id) });
      void queryClient.invalidateQueries({ queryKey: helmKeys.cockpit(organizationId, book.id) });
      toast.success("Saved. Helm is re-planning.");
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not save this.")),
  });
  const confirmed = row.msme_source === "PERSON" ? row.msme_category ?? "" : "";
  return (
    <tr className="helm-hairline border-t">
      <th scope="row" className="py-2 pr-3 text-left font-medium">
        {row.name}
        {row.udyam_number ? <span className="helm-muted helm-figure block text-[11px]">{row.udyam_number}</span> : null}
      </th>
      <td className="py-2 pr-3 text-xs">{row.role === "SUPPLIER" ? "Supplier" : row.role === "CUSTOMER" ? "Customer" : "Both"}</td>
      <td className="py-2 pr-3 text-xs">{row.msme_label}</td>
      <td className="py-2">
        {row.role !== "CUSTOMER" ? (
          <label>
            <span className="sr-only">MSME category for {row.name}</span>
            <select
              className="helm-input h-8 min-h-0 w-40 py-0 text-xs"
              value={confirmed}
              disabled={!writable || save.isPending}
              onChange={(event) => save.mutate(event.target.value)}
            >
              {MSME_CHOICES.map((choice) => (
                <option key={choice.value} value={choice.value}>
                  {choice.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </td>
    </tr>
  );
};

export const HelmSettings: React.FC = () => {
  const { organizationId, canDecide, role } = useHelm();
  const book = useHelmBook();
  const settings = useQuery({
    queryKey: helmKeys.settings(organizationId, book.id),
    queryFn: () => getHelmSettings(organizationId, book.id),
  });
  const counterparties = useQuery({
    queryKey: helmKeys.counterparties(organizationId, book.id),
    queryFn: () => listHelmCounterparties(organizationId, book.id),
  });
  const writable = canDecide && !book.read_only;

  if (settings.isError) {
    return <LoadFailed what="Helm's settings" onRetry={() => void settings.refetch()} />;
  }
  if (!settings.data) {
    return (
      <p className="helm-muted text-sm" role="status">
        Loading…
      </p>
    );
  }
  const suppliers = (counterparties.data ?? []).filter((c) => c.name !== "hidden");
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="helm-muted mt-1 text-sm">For {book.name}. Changes are audited and Helm re-plans after each one.</p>
      </div>
      <Identities settings={settings.data} writable={writable} />
      <Cash settings={settings.data} canEnter={!book.read_only && (canDecide || role === "BILLING")} />
      <Planning settings={settings.data} writable={writable} />
      <section aria-labelledby="helm-counterparties" className="helm-panel p-4">
        <h2 id="helm-counterparties" className="text-sm font-semibold">
          Suppliers and customers
        </h2>
        <p className="helm-muted mt-1 text-xs">
          Confirm which suppliers are micro or small enterprises: Helm pays them within the legal limit (MSMED Act section
          15). A supplier whose category is not confirmed is treated as micro or small, to be safe.
        </p>
        {suppliers.length === 0 ? (
          <p className="mt-3 text-sm">None yet. They appear as Helm reads your invoices.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="helm-label text-left">
                  <th scope="col" className="py-1 pr-3">Name</th>
                  <th scope="col" className="py-1 pr-3">Role</th>
                  <th scope="col" className="py-1 pr-3">MSME status</th>
                  <th scope="col" className="py-1">Confirm</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.map((row) => (
                  <CounterpartyRow key={row.id} row={row} writable={writable} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default HelmSettings;
