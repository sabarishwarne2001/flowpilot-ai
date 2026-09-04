/**
 * ARCH-27 — the partner portal.
 *
 * WHAT THIS SCREEN REFUSES TO DO
 * ==============================
 *
 * It never renders an unknown supplier cost as a number. `formatMicros(null)`
 * returns an em dash, and every money cell whose backing field is nullable
 * goes through it. A reseller looking at this page must be able to tell the
 * difference between "we made no margin" and "nobody knows what this cost",
 * because only one of those is worth negotiating about.
 *
 * It never recomputes the statement digest. `digest_matches` arrives from the
 * server, which recomputed the SHA-256 over the canonical payload at request
 * time. The badge reflects that verdict and nothing else.
 *
 * WHY THE ZERO_BYOK ROW IS VISUALLY DISTINCT AND NOT MERGED
 * ========================================================
 *
 * Invariant 4. BYOK revenue carries 100% margin because the tenant pays the
 * model provider directly, and a book that is 60% BYOK has a completely
 * different cost profile from one that is 0%. A single blended margin
 * percentage hides exactly the thing a partner needs to see.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  FileText,
  Loader2,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";

import { LoadingScreen } from "@/components/common/LoadingScreen";
import { partnerApi } from "@/services/api/partner";
import { partnerKeys } from "@/services/api/queryKeys";
import {
  BASIS_CLASS_LABEL,
  formatBps,
  formatMicros,
  type PayoutPeriod,
  type RevShareLedgerLine,
} from "@/types/partner";

type Tab = "book" | "ledger" | "payouts";

const TONE_CLASS: Record<string, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
};

const StatusPill = ({ status }: { status: PayoutPeriod["status"] }) => {
  const tone =
    status === "PAID"
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : status === "SEALED"
        ? "bg-blue-50 text-blue-700 ring-blue-200"
        : status === "VOID"
          ? "bg-red-50 text-red-700 ring-red-200"
          : "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}
    >
      {status}
    </span>
  );
};

const BasisClassPill = ({
  basisClass,
}: {
  basisClass: RevShareLedgerLine["basis_class"];
}) => {
  const meta = BASIS_CLASS_LABEL[basisClass];
  return (
    <span
      title={meta.hint}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        TONE_CLASS[meta.tone] ?? TONE_CLASS.slate
      }`}
    >
      {meta.label}
    </span>
  );
};

const Metric = ({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) => (
  <div className="rounded-lg border border-slate-200 bg-white p-4">
    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
      {label}
    </p>
    <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
  </div>
);

export default function PartnerPortal() {
  const [tab, setTab] = useState<Tab>("book");
  const [selectedPeriodId, setSelectedPeriodId] = useState<string | null>(null);

  const partnersQuery = useQuery({
    queryKey: partnerKeys.mine(),
    queryFn: partnerApi.listMine,
  });

  // A user may hold memberships in more than one partner. The first is a
  // default, not an assumption: a switcher belongs here once that is common.
  const partnerId = partnersQuery.data?.[0]?.id ?? null;

  const economicsQuery = useQuery({
    queryKey: partnerId ? partnerKeys.economics(partnerId) : ["partner", "none"],
    queryFn: () => partnerApi.getEconomics(partnerId as string),
    enabled: Boolean(partnerId),
  });

  const bookQuery = useQuery({
    queryKey: partnerId ? partnerKeys.book(partnerId) : ["partner", "none"],
    queryFn: () => partnerApi.getBook(partnerId as string),
    enabled: Boolean(partnerId) && tab === "book",
  });

  const payoutsQuery = useQuery({
    queryKey: partnerId ? partnerKeys.payouts(partnerId) : ["partner", "none"],
    queryFn: () => partnerApi.listPayouts(partnerId as string),
    enabled: Boolean(partnerId) && (tab === "payouts" || tab === "ledger"),
  });

  const effectivePeriodId =
    selectedPeriodId ?? payoutsQuery.data?.[0]?.id ?? null;

  const statementQuery = useQuery({
    queryKey:
      partnerId && effectivePeriodId
        ? partnerKeys.statement(partnerId, effectivePeriodId)
        : ["partner", "none"],
    queryFn: () =>
      partnerApi.getStatement(partnerId as string, effectivePeriodId as string),
    enabled: Boolean(partnerId && effectivePeriodId) && tab === "ledger",
  });

  const economics = economicsQuery.data;
  const currency = economics?.currency ?? "USD";

  const byokShare = useMemo(() => {
    if (!economics) {
      return "—";
    }
    return formatBps(economics.zero_byok_revenue_share_bps);
  }, [economics]);

  if (partnersQuery.isLoading) {
    return <LoadingScreen />;
  }

  if (!partnerId) {
    return (
      <div className="mx-auto max-w-2xl p-12 text-center">
        <Building2 className="mx-auto h-10 w-10 text-slate-300" />
        <h1 className="mt-4 text-lg font-semibold text-slate-900">
          No partner account
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Your user is not a member of a reseller partner. If you believe this
          is wrong, ask your partner owner to add you.
        </p>
      </div>
    );
  }

  const partner = partnersQuery.data?.[0];

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">
          {partner?.name}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Book of business, revenue share and payout statements.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Client accounts"
          value={String(economics?.organization_count ?? 0)}
        />
        <Metric
          label="Lifetime revenue"
          value={formatMicros(
            economics?.lifetime_revenue_micros ?? null,
            currency,
          )}
          hint="Sealed periods only"
        />
        <Metric
          label="Lifetime margin"
          /* null renders as an em dash. A lifetime margin nobody computed is
             not a lifetime margin of nil. */
          value={formatMicros(economics?.lifetime_margin_micros ?? null, currency)}
        />
        <Metric
          label="BYOK share of revenue"
          value={byokShare}
          hint="100% margin — tenant pays the provider directly"
        />
      </section>

      {economics && economics.lifetime_excluded_revenue_micros > 0 ? (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div className="text-sm text-amber-900">
            <p className="font-medium">
              {formatMicros(
                economics.lifetime_excluded_revenue_micros,
                currency,
              )}{" "}
              of revenue is excluded from payout.
            </p>
            <p className="mt-1">
              The supplier cost behind it is unknown or partial, so margin on it
              is only an upper bound. Nothing is paid on an upper bound — the
              revenue is recorded and set aside rather than quietly counted as
              free.
            </p>
          </div>
        </div>
      ) : null}

      <nav className="flex gap-1 border-b border-slate-200">
        {(
          [
            ["book", "Book of business", Building2],
            ["ledger", "Rev-share ledger", TrendingUp],
            ["payouts", "Payout statements", FileText],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium ${
              tab === key
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </nav>

      {tab === "book" ? (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          {bookQuery.isLoading ? (
            <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading book…
            </div>
          ) : (bookQuery.data?.length ?? 0) === 0 ? (
            <p className="p-6 text-sm text-slate-500">
              No client accounts assigned yet.
            </p>
          ) : (
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Organization</th>
                  <th className="px-4 py-2">Slug</th>
                  <th className="px-4 py-2">Since</th>
                  <th className="px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {bookQuery.data?.map((entry) => (
                  <tr key={entry.id}>
                    <td className="px-4 py-2 font-medium text-slate-900">
                      {entry.organization_name}
                    </td>
                    <td className="px-4 py-2 text-slate-500">
                      {entry.organization_slug}
                    </td>
                    <td className="px-4 py-2 text-slate-500">
                      {new Date(entry.effective_from).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-2">{entry.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}

      {tab === "ledger" ? (
        <section className="space-y-4">
          <div className="flex items-center gap-3">
            <label
              htmlFor="period"
              className="text-sm font-medium text-slate-700"
            >
              Period
            </label>
            <select
              id="period"
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
              value={effectivePeriodId ?? ""}
              onChange={(event) => setSelectedPeriodId(event.target.value)}
            >
              {payoutsQuery.data?.map((period) => (
                <option key={period.id} value={period.id}>
                  {period.period_start} — {period.period_end} ({period.status})
                </option>
              ))}
            </select>
          </div>

          {statementQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading statement…
            </div>
          ) : statementQuery.data ? (
            <>
              {/* The verdict travels from the server. The browser does not
                  recompute the hash — see the module docstring. */}
              {statementQuery.data.period.status !== "DRAFT" ? (
                <div
                  className={`flex items-center gap-2 rounded-lg border p-3 text-sm ${
                    statementQuery.data.digest_matches
                      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                      : "border-red-200 bg-red-50 text-red-800"
                  }`}
                >
                  {statementQuery.data.digest_matches ? (
                    <ShieldCheck className="h-4 w-4" />
                  ) : (
                    <AlertTriangle className="h-4 w-4" />
                  )}
                  <span>
                    {statementQuery.data.digest_matches
                      ? "Statement digest verified against the sealed figures."
                      : "Statement digest does NOT match the stored figures. Do not pay on this statement; raise it with the platform."}
                  </span>
                </div>
              ) : (
                <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                  This period is still a draft. It has no digest yet and its
                  figures can still change.
                </p>
              )}

              <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-4 py-2">Client</th>
                      <th className="px-4 py-2">Class</th>
                      <th className="px-4 py-2 text-right">Revenue</th>
                      <th className="px-4 py-2 text-right">Supplier cost</th>
                      <th className="px-4 py-2 text-right">Margin</th>
                      <th className="px-4 py-2 text-right">Rate</th>
                      <th className="px-4 py-2 text-right">Payout</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {statementQuery.data.lines.map((line) => (
                      <tr key={line.id}>
                        <td className="px-4 py-2 font-medium text-slate-900">
                          {line.organization_name ?? line.organization_id}
                        </td>
                        <td className="px-4 py-2">
                          <BasisClassPill basisClass={line.basis_class} />
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {formatMicros(line.revenue_micros, currency)}
                        </td>
                        {/* null, not 0. */}
                        <td className="px-4 py-2 text-right tabular-nums text-slate-600">
                          {formatMicros(line.supplier_cost_micros, currency)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-600">
                          {formatMicros(line.margin_micros, currency)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-500">
                          {formatBps(line.share_bps)}
                        </td>
                        <td className="px-4 py-2 text-right font-medium tabular-nums text-slate-900">
                          {formatMicros(line.payout_micros, currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="text-sm text-slate-500">No statement selected.</p>
          )}
        </section>
      ) : null}

      {tab === "payouts" ? (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          {payoutsQuery.isLoading ? (
            <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading statements…
            </div>
          ) : (payoutsQuery.data?.length ?? 0) === 0 ? (
            <p className="p-6 text-sm text-slate-500">
              No payout periods computed yet.
            </p>
          ) : (
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Period</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2 text-right">Revenue</th>
                  <th className="px-4 py-2 text-right">Margin</th>
                  <th className="px-4 py-2 text-right">BYOK revenue</th>
                  <th className="px-4 py-2 text-right">Excluded</th>
                  <th className="px-4 py-2 text-right">Payout</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {payoutsQuery.data?.map((period) => (
                  <tr key={period.id}>
                    <td className="px-4 py-2 font-medium text-slate-900">
                      {period.period_start} — {period.period_end}
                    </td>
                    <td className="px-4 py-2">
                      <StatusPill status={period.status} />
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {formatMicros(period.gross_revenue_micros, period.currency)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-slate-600">
                      {formatMicros(period.margin_micros, period.currency)}
                    </td>
                    {/* Invariant 4 on the statement list, not only inside it. */}
                    <td className="px-4 py-2 text-right tabular-nums text-emerald-700">
                      {formatMicros(
                        period.zero_byok_revenue_micros,
                        period.currency,
                      )}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-amber-700">
                      {formatMicros(
                        period.excluded_revenue_micros,
                        period.currency,
                      )}
                    </td>
                    <td className="px-4 py-2 text-right font-medium tabular-nums text-slate-900">
                      <span className="inline-flex items-center gap-1">
                        {period.status === "PAID" ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                        ) : null}
                        {formatMicros(period.payout_micros, period.currency)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}
    </div>
  );
}
