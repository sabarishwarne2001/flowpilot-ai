/**
 * ARCH50-S2:page-revops — the operator's Revenue Operations console (platform superadmins).
 *
 *   Revenue      MRR / ARR per currency (never summed across currencies), movements over 30 days, receivables,
 *                promo redemptions, unpriced subscriptions (counted, never priced at zero), frozen monthly snapshots
 *   Price books  plan prices per interval and currency (annual, INR): draft, fill, publish (immutable)
 *   Promo codes  percent or amount off, once / repeating / forever, caps, deadlines, scope
 *   Contracts    invoiced enterprise contracts: draft, activate (assigns the plan), issue, pay, void, end
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, BookOpen, FileSignature, Loader2, RefreshCw, TicketPercent, TrendingUp } from "lucide-react";
import { toast } from "sonner";

import {
  BUTTON_DESTRUCTIVE, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, SCROLL_X,
  SECTION_TITLE, SELECT, SURFACE, SURFACE_INSET, TABLE_HEAD, TABLE_ROW,
} from "@/components/ui/primitives";
import { ApiError, errorMessage } from "@/services/api/errors";
import {
  activateContract, createContract, createPriceBook, createPromoCode, endContract, getContract, getPriceBook,
  getRevenueMetrics, issueContractInvoices, listContracts, listPriceBooks, listPromoCodes, listRevOpsOrganizations,
  payInvoice,
  publishPriceBook, revopsKeys, runRevOpsSweep, setPriceEntry, setPromoActive, voidInvoice,
} from "@/services/api/revops";
import {
  CONTRACT_INTERVALS, CURRENCIES, PLAN_INTERVALS, PROMO_DURATIONS, REVOPS_MESSAGES, money,
  type ContractInterval, type Currency, type PlanInterval, type PromoDuration, type RevOpsCode,
} from "@/types/revops";
import { formatCalendarDate, formatTimestamp } from "@/utils/displayTime";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabList, TabPanel, useUrlTab, type TabDefinition } from "@/components/ui/Tabs";

type Tab = "revenue" | "books" | "promos" | "contracts";
const TABS: readonly TabDefinition<Tab>[] = [
  { id: "revenue", label: "Revenue", icon: TrendingUp },
  { id: "books", label: "Price books", icon: BookOpen },
  { id: "promos", label: "Promo codes", icon: TicketPercent },
  { id: "contracts", label: "Contracts", icon: FileSignature },
];
const TAB_IDS = TABS.map((tab) => tab.id);
const COUNT = new Intl.NumberFormat();
const ACT_DONE: Readonly<Record<"activate" | "issue" | "end" | "pay" | "void", string>> = {
  activate: "Contract activated. The organization is on the contract's plan.",
  issue: "Every invoice that is due has been issued.",
  end: "Contract cancelled.",
  pay: "Invoice marked paid.",
  void: "Invoice voided.",
};
const TIERS = ["free", "developer", "business", "enterprise"] as const;

const failure = (error: unknown, fallback: string): string => {
  if (error instanceof ApiError && error.code && error.code in REVOPS_MESSAGES) {
    return REVOPS_MESSAGES[error.code as RevOpsCode] ?? fallback;
  }
  return errorMessage(error, fallback);
};

const RevenueTab: React.FC = () => {
  const queryClient = useQueryClient();
  const metrics = useQuery({ queryKey: revopsKeys.metrics(), queryFn: getRevenueMetrics });
  const sweep = useMutation({
    mutationFn: runRevOpsSweep,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: revopsKeys.all() });
      toast.success(`Sweep: ${result.invoices_issued} invoice(s) issued, ${result.contracts_ended} contract(s) ended, ${result.redemptions_confirmed} redemption(s) confirmed.`);
    },
    onError: (error) => toast.error(failure(error, "The sweep failed.")),
  });
  const m = metrics.data;
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button type="button" className={BUTTON_SECONDARY} disabled={sweep.isPending} onClick={() => sweep.mutate()}>
          {sweep.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden />}Run the daily sweep now
        </button>
      </div>
      {metrics.isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {metrics.isError ? <p role="alert" className="text-sm text-destructive">{failure(metrics.error, "Metrics could not be loaded.")}</p> : null}
      {m ? (
        <>
          <section className="grid gap-3 md:grid-cols-2" data-testid="revops-currencies">
            {Object.entries(m.currencies).map(([currency, c]) => (
              <div key={currency} className={`${SURFACE} space-y-2 p-4`}>
                <h2 className={SECTION_TITLE}>{currency}</h2>
                <p className="text-2xl font-semibold tabular-nums">{money(c.mrr_micros, currency)} <span className={HINT}>MRR</span></p>
                <p className="text-sm tabular-nums">{money(c.arr_micros, currency)} ARR · {COUNT.format(c.customers)} customer{c.customers === 1 ? "" : "s"} ({COUNT.format(c.contract_customers)} on contract) · ARPA {money(c.arpa_micros, currency)}</p>
                <p className={HINT}>Last {m.compare_days} days: new {money(c.movements.new, currency)} · expansion {money(c.movements.expansion, currency)} · contraction {money(c.movements.contraction, currency)} · churned {money(c.movements.churned, currency)}</p>
                <p className={HINT}>Receivables: {money(m.receivables[currency]?.outstanding_micros ?? 0, currency)} outstanding, {money(m.receivables[currency]?.overdue_micros ?? 0, currency)} overdue ({COUNT.format(m.receivables[currency]?.overdue_invoices ?? 0)} invoices)</p>
              </div>
            ))}
          </section>
          {m.unpriced_subscriptions.length ? (
            <p className="text-sm text-amber-700 dark:text-amber-400">{COUNT.format(m.unpriced_subscriptions.length)} live subscription(s) have no recorded price and are counted as unpriced, not as zero.</p>
          ) : null}
          <section className={`${SURFACE} ${SCROLL_X} p-4`}>
            <h2 className={SECTION_TITLE}>Monthly snapshots</h2>
            <table className="mt-2 w-full text-sm">
              <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Month</th><th className="pr-3">Currency</th><th className="pr-3 text-right">MRR</th><th className="pr-3 text-right">New</th><th className="pr-3 text-right">Expansion</th><th className="pr-3 text-right">Contraction</th><th className="pr-3 text-right">Churned</th><th className="pr-3 text-right">Customers</th></tr></thead>
              <tbody>
                {m.snapshots.map((row) => {
                  const currency = String(row["currency"]);
                  return (
                    <tr key={`${String(row["month"])}-${currency}`} className={TABLE_ROW}>
                      <td className="py-2 pr-3">{formatCalendarDate(String(row["month"]))}</td>
                      <td className="pr-3">{currency}</td>
                      <td className="pr-3 text-right tabular-nums">{money(Number(row["mrr_micros"]), currency)}</td>
                      <td className="pr-3 text-right tabular-nums">{money(Number(row["new_mrr_micros"]), currency)}</td>
                      <td className="pr-3 text-right tabular-nums">{money(Number(row["expansion_mrr_micros"]), currency)}</td>
                      <td className="pr-3 text-right tabular-nums">{money(Number(row["contraction_mrr_micros"]), currency)}</td>
                      <td className="pr-3 text-right tabular-nums">{money(Number(row["churned_mrr_micros"]), currency)}</td>
                      <td className="pr-3 text-right tabular-nums">{COUNT.format(Number(row["active_customers"]))}</td>
                    </tr>
                  );
                })}
                {m.snapshots.length === 0 ? <tr><td colSpan={8} className={`py-3 ${HINT}`}>No closed month yet; the sweep freezes each month on the 1st.</td></tr> : null}
              </tbody>
            </table>
          </section>
        </>
      ) : null}
    </div>
  );
};

const BooksTab: React.FC = () => {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [currency, setCurrency] = useState<Currency>("INR");
  const [tier, setTier] = useState<string>("business");
  const [interval, setBillingInterval] = useState<PlanInterval>("year");
  const [amount, setAmount] = useState("");
  const [priceId, setPriceId] = useState("");
  const books = useQuery({ queryKey: revopsKeys.books(), queryFn: listPriceBooks });
  const book = useQuery({ queryKey: revopsKeys.book(selected ?? ""), queryFn: () => getPriceBook(selected ?? ""), enabled: Boolean(selected) });
  const refresh = async (): Promise<void> => { await queryClient.invalidateQueries({ queryKey: revopsKeys.all() }); };
  const create = useMutation({
    mutationFn: () => createPriceBook(code.trim(), currency, null),
    onSuccess: async (result) => { setCode(""); setSelected(result.id); await refresh(); },
    onError: (error) => toast.error(failure(error, "The price book could not be created.")),
  });
  const entry = useMutation({
    mutationFn: () => setPriceEntry(selected ?? "", { tier_key: tier, interval, unit_amount: Math.round(Number(amount) * 100), gateway_price_id: priceId.trim() || null }),
    onSuccess: async () => { setAmount(""); setPriceId(""); await refresh(); },
    onError: (error) => toast.error(failure(error, "The price could not be set.")),
  });
  const publish = useMutation({
    mutationFn: () => publishPriceBook(selected ?? ""),
    onSuccess: async (result) => { await refresh(); toast.success(`${result.code} is published; the previous ${result.currency} book is retired.`); },
    onError: (error) => toast.error(failure(error, "The price book could not be published.")),
  });
  const detail = book.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <section className={`${SURFACE} space-y-3 p-4`}>
        <h2 className={SECTION_TITLE}>Plan price books</h2>
        <ul className="space-y-1">
          {(books.data ?? []).map((b) => (
            <li key={b.id}>
              <button type="button" onClick={() => setSelected(b.id)} className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${selected === b.id ? "bg-muted" : "hover:bg-muted/50"}`}>
                <span className="font-mono">{b.code}</span> · {b.currency} · {b.status} · {COUNT.format(b.entries)} price{b.entries === 1 ? "" : "s"}
              </button>
            </li>
          ))}
          {(books.data?.length ?? 0) === 0 ? <li className={HINT}>No price book yet.</li> : null}
        </ul>
        <form className={`${SURFACE_INSET} space-y-2 p-3`} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <label className="block space-y-1"><span className={FIELD_LABEL}>New book code</span>
            <input className={INPUT} value={code} onChange={(e) => setCode(e.target.value)} placeholder="INR-2026" pattern="[A-Za-z0-9][A-Za-z0-9_\-]{1,31}" required /></label>
          <label className="block space-y-1"><span className={FIELD_LABEL}>Currency</span>
            <select className={SELECT} value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>
          <button type="submit" className={BUTTON_PRIMARY} disabled={create.isPending}>Draft book</button>
        </form>
      </section>
      <section className={`${SURFACE} space-y-3 p-4`}>
        {detail ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className={SECTION_TITLE}><span className="font-mono">{detail.code}</span> · {detail.currency} · {detail.status}</h2>
              {detail.status === "DRAFT" ? (
                <button type="button" className={BUTTON_PRIMARY} disabled={publish.isPending || detail.entries.length === 0} onClick={() => publish.mutate()}>Publish</button>
              ) : <span className={HINT}>Published {formatTimestamp(detail.published_at)} · digest {detail.content_digest?.slice(0, 12)}</span>}
            </div>
            <div className={SCROLL_X}>
              <table className="w-full text-sm">
                <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Plan</th><th className="pr-3">Interval</th><th className="pr-3 text-right">Price per seat</th><th className="pr-3">Gateway price</th></tr></thead>
                <tbody>
                  {detail.entries.map((e) => (
                    <tr key={e.id} className={TABLE_ROW}>
                      <td className="py-2 pr-3">{e.tier_key}</td><td className="pr-3">{e.billing_interval}</td>
                      <td className="pr-3 text-right tabular-nums">{money(e.unit_amount_micros, detail.currency)}</td>
                      <td className="pr-3 font-mono text-xs">{e.gateway_price_id ?? "— (not sold online)"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {detail.status === "DRAFT" ? (
              <form className="grid gap-2 sm:grid-cols-5 sm:items-end" onSubmit={(e) => { e.preventDefault(); entry.mutate(); }}>
                <label className="space-y-1"><span className={FIELD_LABEL}>Plan</span>
                  <select className={SELECT} value={tier} onChange={(e) => setTier(e.target.value)}>{TIERS.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
                <label className="space-y-1"><span className={FIELD_LABEL}>Interval</span>
                  <select className={SELECT} value={interval} onChange={(e) => setBillingInterval(e.target.value as PlanInterval)}>{PLAN_INTERVALS.map((i) => <option key={i} value={i}>{i}</option>)}</select></label>
                <label className="space-y-1"><span className={FIELD_LABEL}>Price ({detail.currency})</span>
                  <input className={INPUT} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
                <label className="space-y-1"><span className={FIELD_LABEL}>Gateway price id</span>
                  <input className={INPUT} value={priceId} onChange={(e) => setPriceId(e.target.value)} /></label>
                <button type="submit" className={BUTTON_SECONDARY} disabled={entry.isPending || !(Number(amount) >= 0) || amount.trim() === ""}>Set price</button>
              </form>
            ) : <p className={HINT}>A published book is immutable; draft a new one to change prices.</p>}
          </>
        ) : <p className={HINT}>Choose a book, or draft one.</p>}
      </section>
    </div>
  );
};

const PromosTab: React.FC = () => {
  const queryClient = useQueryClient();
  const promos = useQuery({ queryKey: revopsKeys.promos(), queryFn: listPromoCodes });
  const [code, setCode] = useState("");
  const [kind, setKind] = useState<"percent" | "amount">("percent");
  const [value, setValue] = useState("");
  const [currency, setCurrency] = useState<Currency>("INR");
  const [duration, setDuration] = useState<PromoDuration>("ONCE");
  const [months, setMonths] = useState("3");
  const [cap, setCap] = useState("");
  const [coupon, setCoupon] = useState("");
  const create = useMutation({
    mutationFn: () => createPromoCode({
      code: code.trim(), duration,
      ...(kind === "percent" ? { percent_off: Number(value) } : { amount_off: Math.round(Number(value) * 100), currency }),
      ...(duration === "REPEATING" ? { duration_in_months: Number(months) } : {}),
      ...(cap.trim() ? { max_redemptions: Number(cap) } : {}),
      ...(coupon.trim() ? { gateway_coupon_id: coupon.trim() } : {}),
    }),
    onSuccess: async () => { setCode(""); setValue(""); await queryClient.invalidateQueries({ queryKey: revopsKeys.promos() }); toast.success("Promo code created."); },
    onError: (error) => toast.error(failure(error, "The promo code could not be created.")),
  });
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => setPromoActive(id, active),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: revopsKeys.promos() }); },
    onError: (error) => toast.error(failure(error, "The promo code could not be changed.")),
  });
  return (
    <div className="space-y-4">
      <section className={`${SURFACE} ${SCROLL_X} p-4`}>
        <table className="w-full text-sm">
          <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Code</th><th className="pr-3">Discount</th><th className="pr-3">Duration</th><th className="pr-3 text-right">Redeemed</th><th className="pr-3">Online</th><th className="pr-3">Active</th></tr></thead>
          <tbody>
            {(promos.data ?? []).map((p) => (
              <tr key={p.id} className={TABLE_ROW}>
                <td className="py-2 pr-3 font-mono">{p.code}</td>
                <td className="pr-3">{p.percent_off !== null ? `${p.percent_off}%` : money(p.amount_off_micros, p.currency)}</td>
                <td className="pr-3">{p.duration === "REPEATING" ? `${p.duration_in_months} months` : p.duration.toLowerCase()}</td>
                <td className="pr-3 text-right tabular-nums">{COUNT.format(p.times_redeemed)}{p.max_redemptions ? ` / ${COUNT.format(p.max_redemptions)}` : ""}</td>
                <td className="pr-3">{p.gateway_coupon_id ? "yes" : "contracts only"}</td>
                <td className="pr-3">
                  <button type="button" className={p.is_active ? BUTTON_DESTRUCTIVE : BUTTON_SECONDARY} disabled={toggle.isPending} onClick={() => toggle.mutate({ id: p.id, active: !p.is_active })}>
                    {p.is_active ? "Deactivate" : "Activate"}
                  </button>
                </td>
              </tr>
            ))}
            {(promos.data?.length ?? 0) === 0 ? <tr><td colSpan={6} className={`py-3 ${HINT}`}>No promo codes.</td></tr> : null}
          </tbody>
        </table>
      </section>
      <form className={`${SURFACE} grid gap-3 p-4 sm:grid-cols-4 sm:items-end`} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <label className="space-y-1"><span className={FIELD_LABEL}>Code</span><input className={INPUT} value={code} onChange={(e) => setCode(e.target.value)} placeholder="LAUNCH20" required /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Kind</span>
          <select className={SELECT} value={kind} onChange={(e) => setKind(e.target.value as "percent" | "amount")}><option value="percent">Percent off</option><option value="amount">Amount off</option></select></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>{kind === "percent" ? "Percent" : "Amount"}</span><input className={INPUT} value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" required /></label>
        {kind === "amount" ? (
          <label className="space-y-1"><span className={FIELD_LABEL}>Currency</span>
            <select className={SELECT} value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>{CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        ) : <span />}
        <label className="space-y-1"><span className={FIELD_LABEL}>Duration</span>
          <select className={SELECT} value={duration} onChange={(e) => setDuration(e.target.value as PromoDuration)}>{PROMO_DURATIONS.map((d) => <option key={d} value={d}>{d.toLowerCase()}</option>)}</select></label>
        {duration === "REPEATING" ? (
          <label className="space-y-1"><span className={FIELD_LABEL}>Months</span><input className={INPUT} value={months} onChange={(e) => setMonths(e.target.value)} inputMode="numeric" /></label>
        ) : <span />}
        <label className="space-y-1"><span className={FIELD_LABEL}>Redemption cap</span><input className={INPUT} value={cap} onChange={(e) => setCap(e.target.value)} placeholder="unlimited" inputMode="numeric" /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Gateway coupon (online)</span><input className={INPUT} value={coupon} onChange={(e) => setCoupon(e.target.value)} placeholder="contracts only" /></label>
        <button type="submit" className={BUTTON_PRIMARY} disabled={create.isPending || code.trim().length < 3 || value.trim() === ""}>Create code</button>
      </form>
    </div>
  );
};

const ContractsTab: React.FC = () => {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const contracts = useQuery({ queryKey: revopsKeys.contracts(), queryFn: listContracts });
  const contract = useQuery({ queryKey: revopsKeys.contract(selected ?? ""), queryFn: () => getContract(selected ?? ""), enabled: Boolean(selected) });
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [form, setForm] = useState({
    organization_id: "", contract_number: "", tier_key: "enterprise", seats: "25", currency: "INR" as Currency,
    billing_interval: "year" as ContractInterval, amount: "", tax: "1800", term_start: today, term_end: "", terms: "30",
    po: "", email: "", promo: "",
  });
  const organizations = useQuery({ queryKey: revopsKeys.organizations(), queryFn: listRevOpsOrganizations });
  // F-186. Paying and voiding asked through window.prompt, and "Cancel" on the prompt still sent
  // the request (with an empty reference: a 422 and "The action failed."). Cancelling a contract
  // asked nothing at all. Each now opens a dialog, and nothing is sent until it is confirmed.
  const [pending, setPending] = useState<
    | { readonly kind: "pay" | "void"; readonly id: string; readonly number: string }
    | { readonly kind: "end"; readonly id: string; readonly number: string }
    | null
  >(null);
  const [answer, setAnswer] = useState("");
  const refresh = async (): Promise<void> => { await queryClient.invalidateQueries({ queryKey: revopsKeys.all() }); };
  const onError = (fallback: string) => (error: unknown) => toast.error(failure(error, fallback));
  const create = useMutation({
    mutationFn: () => createContract({
      organization_id: form.organization_id.trim(), contract_number: form.contract_number.trim(), tier_key: form.tier_key,
      seats: Number(form.seats), currency: form.currency, billing_interval: form.billing_interval,
      amount_per_period: Math.round(Number(form.amount) * 100), tax_rate_bps: Number(form.tax), term_start: form.term_start,
      term_end: form.term_end, payment_terms_days: Number(form.terms), po_number: form.po.trim() || undefined,
      billing_email: form.email.trim() || undefined, promo_code: form.promo.trim() || undefined,
    }),
    onSuccess: async (result) => { setSelected(result.id); await refresh(); toast.success(`Contract ${result.contract_number} drafted.`); },
    onError: onError("The contract could not be created."),
  });
  const act = useMutation({
    mutationFn: async (action: { readonly kind: "activate" | "issue" | "end" | "pay" | "void"; readonly id: string; readonly text?: string }) => {
      if (action.kind === "activate") { return activateContract(action.id); }
      if (action.kind === "issue") { return issueContractInvoices(action.id); }
      if (action.kind === "end") { return endContract(action.id, "CANCELLED"); }
      if (action.kind === "pay") { return payInvoice(action.id, action.text ?? ""); }
      return voidInvoice(action.id, action.text ?? "");
    },
    onSuccess: async (_contract, action) => {
      setPending(null);
      setAnswer("");
      await refresh();
      toast.success(ACT_DONE[action.kind]);
    },
    onError: onError("The action failed."),
  });
  const answerValid = pending?.kind === "pay" ? answer.trim().length >= 1 : answer.trim().length >= 3;
  const confirmPending = (): void => {
    if (!pending) { return; }
    if (pending.kind === "end") { act.mutate({ kind: "end", id: pending.id }); return; }
    if (answerValid) { act.mutate({ kind: pending.kind, id: pending.id, text: answer.trim() }); }
  };
  const closePending = (): void => { if (!act.isPending) { setPending(null); setAnswer(""); } };
  const c = contract.data;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));
  return (
    <div className="space-y-4">
      <section className={`${SURFACE} ${SCROLL_X} p-4`}>
        <table className="w-full text-sm">
          <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Contract</th><th className="pr-3">Organization</th><th className="pr-3">Plan</th><th className="pr-3 text-right">Per period</th><th className="pr-3">Term</th><th className="pr-3">Status</th><th className="pr-3 text-right">Open / overdue</th></tr></thead>
          <tbody>
            {(contracts.data ?? []).map((row) => (
              <tr key={row.id} className={`${TABLE_ROW} cursor-pointer`} onClick={() => setSelected(row.id)}>
                <td className="py-2 pr-3 font-mono">{row.contract_number}</td>
                <td className="pr-3">{row.organization_name ?? row.organization_id}</td>
                <td className="pr-3">{row.tier_key} · {COUNT.format(row.seats)} seats</td>
                <td className="pr-3 text-right tabular-nums">{money(row.amount_per_period_micros, row.currency)} / {row.billing_interval}</td>
                <td className="pr-3">{formatCalendarDate(row.term_start)} – {formatCalendarDate(row.term_end)}</td>
                <td className="pr-3">{row.status}</td>
                <td className="pr-3 text-right tabular-nums">{COUNT.format(row.open_invoices)} / {COUNT.format(row.overdue_invoices)}</td>
              </tr>
            ))}
            {(contracts.data?.length ?? 0) === 0 ? <tr><td colSpan={7} className={`py-3 ${HINT}`}>No contracts.</td></tr> : null}
          </tbody>
        </table>
      </section>
      {c ? (
        <section className={`${SURFACE} space-y-3 p-4`} data-testid="contract-detail">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className={SECTION_TITLE}><span className="font-mono">{c.contract_number}</span> · {c.organization_name} · {c.status}</h2>
            <div className="flex gap-2">
              {c.status === "DRAFT" ? <button type="button" className={BUTTON_PRIMARY} disabled={act.isPending} onClick={() => act.mutate({ kind: "activate", id: c.id })}>Activate</button> : null}
              {c.status === "ACTIVE" ? <button type="button" className={BUTTON_SECONDARY} disabled={act.isPending} onClick={() => act.mutate({ kind: "issue", id: c.id })}>Issue due invoices</button> : null}
              {c.status === "ACTIVE" || c.status === "DRAFT" ? <button type="button" className={BUTTON_DESTRUCTIVE} disabled={act.isPending} onClick={() => setPending({ kind: "end", id: c.id, number: c.contract_number })}>Cancel contract</button> : null}
            </div>
          </div>
          <p className={HINT}>{money(c.amount_per_period_micros, c.currency)} per {c.billing_interval} · tax {(c.tax_rate_bps / 100).toFixed(2)}% · net {c.payment_terms_days} days{c.po_number ? ` · PO ${c.po_number}` : ""}</p>
          <div className={SCROLL_X}>
            <table className="w-full text-sm">
              <thead><tr className={TABLE_HEAD}><th className="py-2 pr-3">Invoice</th><th className="pr-3">Period</th><th className="pr-3 text-right">Subtotal</th><th className="pr-3 text-right">Discount</th><th className="pr-3 text-right">Tax</th><th className="pr-3 text-right">Total</th><th className="pr-3">Due</th><th className="pr-3">Status</th><th /></tr></thead>
              <tbody>
                {c.invoices.map((inv) => (
                  <tr key={inv.id} className={TABLE_ROW}>
                    <td className="py-2 pr-3 font-mono">{inv.invoice_number}</td>
                    <td className="pr-3">{formatCalendarDate(inv.period_start)} – {formatCalendarDate(inv.period_end)}</td>
                    <td className="pr-3 text-right tabular-nums">{money(inv.subtotal_micros, inv.currency)}</td>
                    <td className="pr-3 text-right tabular-nums">{money(inv.discount_micros, inv.currency)}</td>
                    <td className="pr-3 text-right tabular-nums">{money(inv.tax_micros, inv.currency)}</td>
                    <td className="pr-3 text-right tabular-nums font-semibold">{money(inv.total_micros, inv.currency)}</td>
                    <td className="pr-3">{formatTimestamp(inv.due_at)}</td>
                    <td className={`pr-3 ${inv.overdue ? "text-destructive" : ""}`}>{inv.overdue ? "OVERDUE" : inv.status}</td>
                    <td className="space-x-1 text-right">
                      {inv.status === "ISSUED" ? (
                        <>
                          <button type="button" className={BUTTON_SECONDARY} disabled={act.isPending} onClick={() => { setAnswer(""); setPending({ kind: "pay", id: inv.id, number: inv.invoice_number }); }}>Mark paid</button>
                          <button type="button" className={BUTTON_DESTRUCTIVE} disabled={act.isPending} onClick={() => { setAnswer(""); setPending({ kind: "void", id: inv.id, number: inv.invoice_number }); }}>Void</button>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
                {c.invoices.length === 0 ? <tr><td colSpan={9} className={`py-3 ${HINT}`}>No invoice issued yet.</td></tr> : null}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      <form className={`${SURFACE} grid gap-3 p-4 sm:grid-cols-4 sm:items-end`} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <h2 className={`${SECTION_TITLE} sm:col-span-4`}>Draft a contract</h2>
        <label className="space-y-1 sm:col-span-2"><span className={FIELD_LABEL}>Organization</span>
          <select className={SELECT} value={form.organization_id} onChange={set("organization_id")} required>
            <option value="">{organizations.isLoading ? "Loading organizations…" : "Choose an organization…"}</option>
            {(organizations.data ?? []).map((o) => (
              <option key={o.id} value={o.id} disabled={o.has_active_contract}>
                {o.name} · {o.slug}{o.tier_key ? ` · ${o.tier_key}` : ""}{o.has_active_contract ? " · has an active contract" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Contract number</span><input className={INPUT} value={form.contract_number} onChange={set("contract_number")} placeholder="ENT-2026-001" required /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Plan</span><select className={SELECT} value={form.tier_key} onChange={set("tier_key")}>{TIERS.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Seats</span><input className={INPUT} value={form.seats} onChange={set("seats")} inputMode="numeric" /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Currency</span><select className={SELECT} value={form.currency} onChange={set("currency")}>{CURRENCIES.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Billed every</span><select className={SELECT} value={form.billing_interval} onChange={set("billing_interval")}>{CONTRACT_INTERVALS.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Amount per period</span><input className={INPUT} value={form.amount} onChange={set("amount")} inputMode="decimal" required /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Tax (basis points)</span><input className={INPUT} value={form.tax} onChange={set("tax")} inputMode="numeric" /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Term start</span><input type="date" className={INPUT} value={form.term_start} onChange={set("term_start")} required /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Term end</span><input type="date" className={INPUT} value={form.term_end} onChange={set("term_end")} required /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Payment terms (days)</span><input className={INPUT} value={form.terms} onChange={set("terms")} inputMode="numeric" /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>PO number</span><input className={INPUT} value={form.po} onChange={set("po")} /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Billing email</span><input className={INPUT} value={form.email} onChange={set("email")} type="email" /></label>
        <label className="space-y-1"><span className={FIELD_LABEL}>Promo code</span><input className={INPUT} value={form.promo} onChange={set("promo")} /></label>
        <button type="submit" className={BUTTON_PRIMARY} disabled={create.isPending || !form.organization_id || !form.term_end || form.amount.trim() === ""}>Draft contract</button>
      </form>
      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.kind === "pay" ? "Record a payment"
            : pending?.kind === "void" ? `Void invoice ${pending.number}?`
              : `Cancel contract ${pending?.number ?? ""}?`
        }
        message={
          pending?.kind === "pay" ? `Invoice ${pending.number} is marked paid with the reference you enter, as the record of the transfer.`
            : pending?.kind === "void" ? "A void invoice stays on the contract as a record and is never collected. Say why."
              : "The contract ends now: no further invoices are issued and the organization keeps its current plan until it is changed. Invoices already issued stay open."
        }
        tone={pending?.kind === "pay" ? "primary" : "danger"}
        confirmText={pending?.kind === "pay" ? "Mark paid" : pending?.kind === "void" ? "Void invoice" : "Cancel contract"}
        cancelText={pending?.kind === "end" ? "Keep the contract" : "Cancel"}
        loading={act.isPending}
        loadingText="Saving…"
        input={
          pending && pending.kind !== "end"
            ? {
                label: pending.kind === "pay" ? "Payment reference" : "Reason",
                value: answer,
                onChange: setAnswer,
                placeholder: pending.kind === "pay" ? "Bank reference or UTR" : "At least 3 characters",
                maxLength: pending.kind === "pay" ? 128 : 200,
              }
            : undefined
        }
        confirmDisabled={pending !== null && pending.kind !== "end" && !answerValid}
        onConfirm={confirmPending}
        onCancel={closePending}
      />
    </div>
  );
};

const RevOpsConsole: React.FC = () => {
  const [tab, setTab] = useUrlTab<Tab>(TAB_IDS);
  return (
    <div className="mx-auto max-w-7xl space-y-6" data-testid="revops-console">
      <PageHeader
        icon={Banknote}
        eyebrow="Platform"
        title="Revenue operations"
        description="Plan prices (monthly, annual; USD, INR), promo codes, invoiced enterprise contracts and revenue."
      />
      <TabList label="RevOps console" idBase="revops" tabs={TABS} value={tab} onChange={setTab} />
      <TabPanel idBase="revops" id={tab}>
        {tab === "revenue" ? <RevenueTab /> : null}
        {tab === "books" ? <BooksTab /> : null}
        {tab === "promos" ? <PromosTab /> : null}
        {tab === "contracts" ? <ContractsTab /> : null}
      </TabPanel>
    </div>
  );
};

export default RevOpsConsole;
