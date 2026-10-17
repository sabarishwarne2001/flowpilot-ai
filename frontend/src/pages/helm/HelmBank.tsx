import React, { useEffect, useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Check, FileUp, Landmark, Plus, RotateCcw } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money } from "@/components/helm/HelmBits";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { LoadFailed } from "@/components/common/LoadFailed";
import { helmCertificatePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import {
  createHelmBankAccount,
  helmKeys,
  ignoreHelmBankLine,
  importHelmBankStatement,
  listHelmBankAccounts,
  listHelmBankLines,
  matchHelmBankLine,
  previewHelmBankStatement,
  reopenHelmBankLine,
} from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { BankImport, BankLine, BankMapping, BankPreview, HelmBankAccount } from "@/types/helm";

/**
 * Bank statements (owner addition A). Import a bank's CSV or Excel export: the column mapping is
 * confirmed once and saved for the account; each import adds only the lines not seen before, sets
 * the account's cash from its closing balance, and matches payments to invoices when the evidence
 * leaves one answer (the amount, the invoice number or payee in the narration, the planned run).
 * Everything else waits here for a person: confirm a suggestion, or say it is not an invoice
 * payment. Every match becomes a "Record paid" action, certified and undoable.
 */

const ROLES: { key: keyof BankMapping; label: string; help: string }[] = [
  { key: "date", label: "Date", help: "the day the money moved" },
  { key: "description", label: "Narration", help: "the words the bank prints" },
  { key: "reference", label: "Reference", help: "cheque, UTR or reference number" },
  { key: "debit", label: "Money out", help: "withdrawals or debits" },
  { key: "credit", label: "Money in", help: "deposits or credits" },
  { key: "amount", label: "Amount (one column)", help: "when in and out share one column" },
  { key: "drcr", label: "Dr/Cr", help: "the column that says in or out" },
  { key: "balance", label: "Balance", help: "the running balance" },
  { key: "balance_drcr", label: "Balance Dr/Cr", help: "says if the balance is overdrawn" },
];

const DATE_ORDERS: { value: BankMapping["date_format"]; label: string }[] = [
  { value: "DMY", label: "Day first (15/10/2026)" },
  { value: "MDY", label: "Month first (10/15/2026)" },
  { value: "YMD", label: "Year first (2026-10-15)" },
];

const signed = (micros: number, currency: string | null): React.ReactNode => (
  <span className={micros < 0 ? "" : "helm-state-holding"}>
    {micros < 0 ? "out " : "in "}
    <Money micros={Math.abs(micros)} currency={currency} />
  </span>
);

const AddAccount: React.FC<{ readonly onAdded: (account: HelmBankAccount) => void }> = ({ onAdded }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const [name, setName] = useState("");
  const [number, setNumber] = useState("");
  const [currency, setCurrency] = useState("INR");
  const add = useMutation({
    mutationFn: () => createHelmBankAccount(organizationId, book.id, { name, number: number || null, currency }),
    onSuccess: (account) => {
      toast.success(`${account.label} added.`);
      setName("");
      setNumber("");
      onAdded(account);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not add the account.")),
  });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <label className="text-xs">
        <span className="helm-label block">Account name</span>
        <input className="helm-input mt-1 w-52" value={name} placeholder="HDFC current account" onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="text-xs">
        <span className="helm-label block">Account number</span>
        <input className="helm-input helm-figure mt-1 w-44" value={number} onChange={(e) => setNumber(e.target.value)} aria-describedby="helm-bank-last4" />
      </label>
      <label className="text-xs">
        <span className="helm-label block">Currency</span>
        <input className="helm-input helm-figure mt-1 w-16 uppercase" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} />
      </label>
      <button type="submit" className="helm-btn" disabled={!name.trim() || add.isPending}>
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add account
      </button>
      <p id="helm-bank-last4" className="helm-muted basis-full text-xs">
        Helm keeps only the last four digits.
      </p>
    </form>
  );
};

const MappingForm: React.FC<{
  readonly preview: BankPreview;
  readonly mapping: BankMapping;
  readonly onChange: (next: BankMapping) => void;
}> = ({ preview, mapping, onChange }) => {
  const id = useId();
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm">
        Which column is which? Helm guessed from the headings in row {preview.header_row}
        {preview.saved ? " (the mapping saved for this account)" : ""}; it is saved for this account once you import.
      </legend>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {ROLES.map((role) => (
          <label key={role.key} className="text-xs">
            <span className="helm-label block">{role.label}</span>
            <select
              className="helm-input mt-1 w-full"
              value={(mapping[role.key] as string | null | undefined) ?? ""}
              aria-describedby={`${id}-${role.key}`}
              onChange={(event) => onChange({ ...mapping, [role.key]: event.target.value || null })}
            >
              <option value="">{role.key === "date" ? "Choose a column" : "Not in this file"}</option>
              {preview.columns.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
            <span id={`${id}-${role.key}`} className="helm-muted mt-0.5 block">
              {role.help}
            </span>
          </label>
        ))}
      </div>
      <div role="radiogroup" aria-label="Date order" className="flex flex-wrap gap-2">
        {DATE_ORDERS.map((order) => (
          <label key={order.value} className="helm-chip cursor-pointer gap-1.5 py-1">
            <input
              type="radio"
              name={`${id}-order`}
              checked={mapping.date_format === order.value}
              onChange={() => onChange({ ...mapping, date_format: order.value })}
            />
            {order.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
};

const ImportPanel: React.FC<{ readonly accounts: HelmBankAccount[]; readonly onImported: (result: BankImport) => void }> = ({
  accounts,
  onImported,
}) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BankPreview | null>(null);
  const [mapping, setMapping] = useState<BankMapping | null>(null);
  useEffect(() => {
    if (!accountId && accounts[0]) {
      setAccountId(accounts[0].id);
    }
  }, [accountId, accounts]);

  const read = useMutation({
    mutationFn: (args: { file: File; mapping?: BankMapping }) =>
      previewHelmBankStatement(organizationId, book.id, args.file, accountId || undefined, args.mapping),
    onSuccess: (result, args) => {
      setPreview(result);
      if (!args.mapping) {
        setMapping(result.mapping);
      }
    },
    onError: (error) => {
      setPreview(null);
      toast.error(errorMessage(error, "Helm could not read that file."));
    },
  });
  const run = useMutation({
    mutationFn: () => importHelmBankStatement(organizationId, book.id, file as File, accountId, mapping as BankMapping),
    onSuccess: (result) => {
      setFile(null);
      setPreview(null);
      setMapping(null);
      onImported(result);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not import the statement.")),
  });

  const [over, setOver] = useState(false);
  const choose = (chosen: File | null): void => {
    setFile(chosen);
    setPreview(null);
    setMapping(null);
    if (chosen) {
      read.mutate({ file: chosen });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">
          <span className="helm-label block">Account</span>
          <select className="helm-input mt-1" value={accountId} onChange={(event) => setAccountId(event.target.value)}>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.label} ({account.currency})
              </option>
            ))}
          </select>
        </label>
      </div>
      <label
        className="helm-dropzone flex cursor-pointer flex-col items-center justify-center gap-2 px-4 py-8 text-center"
        data-scanning={read.isPending || run.isPending ? "true" : undefined}
        data-over={over ? "true" : undefined}
        data-testid="helm-bank-dropzone"
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const dropped = event.dataTransfer.files?.[0] ?? null;
          if (dropped) {
            choose(dropped);
          }
        }}
      >
        <span className="helm-accent flex h-12 w-12 items-center justify-center rounded-2xl border border-[hsl(var(--helm-accent)/0.35)] bg-[hsl(var(--helm-accent)/0.1)]">
          <FileUp className="h-5 w-5" aria-hidden="true" />
        </span>
        <span className="text-sm font-semibold">{file ? file.name : "Choose a CSV or Excel file, or drop it here"}</span>
        <span className="helm-muted text-xs">
          {read.isPending ? "Scanning the statement…" : run.isPending ? "Importing and matching…" : "Your bank's own export: CSV, XLS or XLSX"}
        </span>
        <input
          type="file"
          className="sr-only"
          aria-label="Statement file"
          accept=".csv,.txt,.xlsx,.xls,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
        />
      </label>
      {read.isPending ? (
        <p className="helm-muted text-sm" role="status">
          Reading the file…
        </p>
      ) : null}
      {preview && mapping ? (
        <div className="space-y-3" data-testid="helm-bank-preview">
          <MappingForm
            preview={preview}
            mapping={mapping}
            onChange={(next) => {
              setMapping(next);
              if (file) {
                read.mutate({ file, mapping: next });
              }
            }}
          />
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <caption className="helm-label py-1 text-left">
                How Helm reads the first lines ({preview.rows} rows in the file)
              </caption>
              <thead>
                <tr className="helm-label">
                  <th scope="col" className="py-1 pr-3">Row</th>
                  <th scope="col" className="py-1 pr-3">Date</th>
                  <th scope="col" className="py-1 pr-3">Narration</th>
                  <th scope="col" className="py-1 pr-3 text-right">Amount</th>
                  <th scope="col" className="py-1 text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {preview.sample.map((line) => (
                  <tr key={line.line_no} className="helm-hairline border-t">
                    <td className="helm-figure py-1 pr-3">{line.line_no}</td>
                    <td className="py-1 pr-3">{formatCalendarDate(line.on)}</td>
                    <td className="py-1 pr-3">{line.description}</td>
                    <td className="helm-figure py-1 pr-3 text-right">{signed(line.amount_micros, accounts.find((a) => a.id === accountId)?.currency ?? null)}</td>
                    <td className="helm-figure py-1 text-right">
                      {line.balance_micros !== null ? <Money micros={line.balance_micros} currency={accounts.find((a) => a.id === accountId)?.currency ?? null} /> : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.problems.length > 0 ? (
            <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-300" aria-label="Lines Helm could not read">
              {preview.problems.map((problem, index) => (
                <li key={index}>
                  {problem.line_no ? `Row ${problem.line_no}: ` : ""}
                  {problem.problem}
                </li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            className="helm-btn helm-btn-primary"
            disabled={!mapping.date || preview.sample.length === 0 || run.isPending || !accountId}
            onClick={() => run.mutate()}
          >
            {run.isPending ? "Importing…" : "Import this statement"}
          </button>
        </div>
      ) : null}
    </div>
  );
};

const Suggestion: React.FC<{ readonly line: BankLine }> = ({ line }) => {
  const { organizationId } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [ignoring, setIgnoring] = useState(false);
  const refresh = (): void => void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
  const match = useMutation({
    mutationFn: (workItemId: string) => matchHelmBankLine(organizationId, line.id, workItemId),
    onSuccess: (done) => {
      toast.success(`Matched to ${done.match.number ?? done.match.document ?? "the invoice"}. Helm records the payment.`);
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not match this line.")),
  });
  const ignore = useMutation({
    mutationFn: () => ignoreHelmBankLine(organizationId, line.id, note),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error, "Helm could not set this line aside.")),
  });
  const suggestions = line.match.suggestions ?? [];
  return (
    <li className="helm-panel-raised helm-row-in p-4" data-testid="helm-bank-line">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm">
            <span className="helm-figure helm-muted mr-2 text-xs">{formatCalendarDate(line.on)}</span>
            {line.description}
            {line.reference ? <span className="helm-muted"> · {line.reference}</span> : null}
          </p>
          <p className="helm-numeral mt-1 text-lg">{signed(line.amount_micros, line.currency)}</p>
          <p className="helm-muted text-xs">{line.account}</p>
        </div>
        <div className="flex shrink-0 flex-col gap-2 sm:items-end">
          {suggestions.length > 0 ? (
            <ul className="space-y-1.5" aria-label="Invoices this line may be">
              {suggestions.map((s, index) =>
                s.work_item_id ? (
                  <li key={s.work_item_id} className="sm:text-right">
                    <button
                      type="button"
                      className="helm-btn"
                      disabled={match.isPending}
                      onClick={() => match.mutate(s.work_item_id as string)}
                      aria-label={`${s.kind === "PAYABLE" ? "It paid" : "It is the payment of"} ${s.number ?? s.document} (${s.counterparty ?? ""})`}
                    >
                      <Check className="h-3.5 w-3.5" aria-hidden="true" />
                      {s.kind === "PAYABLE" ? "It paid" : "Payment of"} {s.number ?? s.document}
                      {s.counterparty ? ` · ${s.counterparty}` : ""}
                    </button>
                    <span className="helm-muted block text-[11px]">{s.reasons.join("; ")}</span>
                  </li>
                ) : (
                  <li key={`hidden-${index}`} className="helm-muted text-xs">
                    An invoice in a workspace you cannot read. Ask a workspace admin for access.
                  </li>
                ),
              )}
            </ul>
          ) : (
            <p className="helm-muted text-xs">No open invoice has this amount.</p>
          )}
          {line.match.warning ? (
            <p role="alert" className="flex max-w-sm items-start gap-1 text-xs text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              {line.match.warning}
            </p>
          ) : null}
          {ignoring ? (
            <form
              className="flex flex-wrap items-center gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                ignore.mutate();
              }}
            >
              <input
                className="helm-input w-48 py-0.5 text-xs"
                aria-label="What is this line?"
                placeholder="Bank charges, salaries, a transfer…"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <button type="submit" className="helm-btn py-0.5" disabled={ignore.isPending}>
                Not an invoice payment
              </button>
            </form>
          ) : (
            <button type="button" className="helm-btn" onClick={() => setIgnoring(true)}>
              Not an invoice payment
            </button>
          )}
        </div>
      </div>
    </li>
  );
};

const ACTION_WORDS: Record<string, string> = {
  APPROVED: "recording…",
  EXECUTING: "recording…",
  EXECUTED: "recorded",
  BLOCKED: "blocked by the Guard",
  FAILED: "could not be recorded",
  UNDONE: "undone",
  REJECTED: "cancelled",
};

/** What reopening does, from the line as it was before: only a recorded payment has a record to undo. */
const reopenedMessage = (line: BankLine): string => {
  if (line.status === "IGNORED") {
    return "Back in the list.";
  }
  if (line.action?.state === "EXECUTED") {
    return "Back in the list: the payment record is undone.";
  }
  if (line.action?.state === "EXECUTING") {
    return "Back in the list.";
  }
  return "Back in the list: nothing had been recorded.";
};

const Matched: React.FC<{ readonly line: BankLine }> = ({ line }) => {
  const { organizationId, withBook, orgSlug } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const reopen = useMutation({
    // The message is chosen when the person clicks, before the line changes.
    mutationFn: (_message: string) => reopenHelmBankLine(organizationId, line.id),
    onSuccess: (_result, message) => {
      toast.success(message);
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not reopen this line.")),
  });
  return (
    <tr className="helm-hairline border-t" data-testid="helm-bank-matched">
      <td className="py-1.5 pr-3 text-xs">{formatCalendarDate(line.on)}</td>
      <td className="py-1.5 pr-3 text-xs">{line.description}</td>
      <td className="helm-figure py-1.5 pr-3 text-right text-xs">{signed(line.amount_micros, line.currency)}</td>
      <td className="py-1.5 pr-3 text-xs">
        {line.status === "IGNORED" ? (
          <span className="helm-muted">Not an invoice payment{line.note ? `: ${line.note}` : ""}</span>
        ) : line.readable ? (
          <>
            {line.match.number ?? line.match.document}
            {line.match.counterparty ? ` · ${line.match.counterparty}` : ""}
            <span className="helm-muted"> ({line.match.by === "HELM" ? "Helm" : "a person"}
              {line.action ? `, ${ACTION_WORDS[line.action.state] ?? line.action.state.toLowerCase()}` : ""})</span>
            {line.action?.certificate_id ? (
              <Link to={withBook(helmCertificatePath(orgSlug, line.action.certificate_id))} className="helm-accent ml-1 hover:underline">
                certificate
              </Link>
            ) : null}
          </>
        ) : (
          <span className="helm-muted">An invoice in a workspace you cannot read</span>
        )}
      </td>
      <td className="py-1.5 text-right">
        {line.readable ? (
          <button type="button" className="helm-btn min-h-0 py-1 text-xs" disabled={reopen.isPending} onClick={() => reopen.mutate(reopenedMessage(line))}
            aria-label={`Reopen the line of ${formatCalendarDate(line.on)}: ${line.description}`}>
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
            Reopen
          </button>
        ) : null}
      </td>
    </tr>
  );
};

export const HelmBank: React.FC = () => {
  const { organizationId, role } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const canImport = !book.read_only && ["OWNER", "ADMIN", "BILLING"].includes(role);
  const accounts = useQuery({
    queryKey: helmKeys.bankAccounts(organizationId, book.id),
    queryFn: () => listHelmBankAccounts(organizationId, book.id),
    enabled: book.readable,
  });
  const waiting = useQuery({
    queryKey: helmKeys.bankLines(organizationId, book.id, "WAITING"),
    queryFn: () => listHelmBankLines(organizationId, book.id, "WAITING"),
    enabled: book.readable,
  });
  const matched = useQuery({
    queryKey: helmKeys.bankLines(organizationId, book.id, "MATCHED"),
    queryFn: () => listHelmBankLines(organizationId, book.id, "MATCHED"),
    enabled: book.readable,
    refetchInterval: (query) =>
      query.state.data?.lines.some((l) => l.action && ["APPROVED", "EXECUTING"].includes(l.action.state)) ? 2_000 : false,
  });
  const ignored = useQuery({
    queryKey: helmKeys.bankLines(organizationId, book.id, "IGNORED"),
    queryFn: () => listHelmBankLines(organizationId, book.id, "IGNORED"),
    enabled: book.readable,
  });
  const [last, setLast] = useState<BankImport | null>(null);
  const refresh = (): void => void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
  const counts = waiting.data?.counts;
  const list = useMemo(() => accounts.data ?? [], [accounts.data]);

  if (!book.readable) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <HelmPageHeader eyebrow={book.name} title="Bank statements" />
        <p className="helm-panel p-4 text-sm">
          This book&apos;s bank statements belong to {book.name}, a workspace you cannot read. Ask a workspace admin for
          access.
        </p>
      </div>
    );
  }
  if (accounts.isError || waiting.isError) {
    return <LoadFailed what="the bank statements" onRetry={() => void Promise.all([accounts.refetch(), waiting.refetch()])} />;
  }
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <HelmPageHeader
        eyebrow={book.name}
        title="Bank statements"
        description="Import your bank's CSV or Excel export. Helm sets the account's cash from the closing balance and matches payments to invoices when the statement leaves one answer; what it cannot be sure of waits below for you. A match records the payment, with the bank line as its evidence, and can be undone."
      />

      <section aria-labelledby="helm-bank-accounts" className="helm-panel p-5">
        <h2 id="helm-bank-accounts" className="helm-label mb-2 flex items-center gap-1.5">
          <Landmark className="h-3.5 w-3.5" aria-hidden="true" />
          Accounts
        </h2>
        {accounts.isLoading ? (
          <p className="helm-muted text-sm" role="status">
            Loading…
          </p>
        ) : list.length === 0 ? (
          <p className="text-sm">No account yet. Add the account your statements come from.</p>
        ) : (
          <ul className="mb-3 grid gap-2 sm:grid-cols-2">
            {list.map((account) => (
              <li key={account.id} className="helm-panel-raised p-3 text-sm" data-testid="helm-bank-account">
                <p className="font-semibold">{account.label}</p>
                <p className="helm-muted text-xs">
                  {account.balance ? (
                    <>
                      <Money micros={account.balance.amount_micros} currency={account.currency} /> on{" "}
                      {formatCalendarDate(account.balance.as_of)}
                    </>
                  ) : (
                    "No statement imported yet"
                  )}
                  {account.waiting > 0 ? ` · ${account.waiting} lines for you to check` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
        {canImport ? <AddAccount onAdded={() => refresh()} /> : null}
      </section>

      {canImport && list.length > 0 ? (
        <section aria-labelledby="helm-bank-import" className="helm-panel p-5">
          <h2 id="helm-bank-import" className="helm-label mb-2">
            Import a statement
          </h2>
          <ImportPanel
            accounts={list}
            onImported={(result) => {
              setLast(result);
              refresh();
            }}
          />
          {last ? (
            <p role="status" className="mt-3 text-sm" data-testid="helm-bank-result">
              {last.lines_new} new {last.lines_new === 1 ? "line" : "lines"}
              {last.lines_skipped > 0 ? ` (${last.lines_skipped} already imported)` : ""}; Helm matched {last.lines_matched};{" "}
              {last.lines_waiting} for you to check.
              {last.closing_balance_micros !== null && last.closing_on ? (
                <>
                  {" "}
                  Closing balance <Money micros={last.closing_balance_micros} currency={list.find((a) => a.id === last.account_id)?.currency ?? null} />{" "}
                  on {formatCalendarDate(last.closing_on)}.
                </>
              ) : null}
            </p>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="helm-bank-waiting" className="helm-panel p-5">
        <h2 id="helm-bank-waiting" className="helm-label mb-1">
          For you to check{counts ? ` (${counts.UNMATCHED + counts.SUGGESTED})` : ""}
        </h2>
        {waiting.isLoading ? (
          <p className="helm-muted text-sm" role="status">
            Loading…
          </p>
        ) : (waiting.data?.lines ?? []).length === 0 ? (
          <p className="text-sm">Nothing waits for you.</p>
        ) : (
          <ul className="grid gap-3 lg:grid-cols-2">
            {(waiting.data?.lines ?? []).map((line) => (
              <Suggestion key={line.id} line={line} />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="helm-bank-matched" className="helm-panel p-5">
        <h2 id="helm-bank-matched" className="helm-label mb-1">
          Matched and set aside
        </h2>
        {[...(matched.data?.lines ?? []), ...(ignored.data?.lines ?? [])].length === 0 ? (
          <p className="helm-muted text-sm">No line is matched yet.</p>
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[620px] text-left">
              <thead>
                <tr className="helm-label">
                  <th scope="col" className="py-1 pr-3">Date</th>
                  <th scope="col" className="py-1 pr-3">Narration</th>
                  <th scope="col" className="py-1 pr-3 text-right">Amount</th>
                  <th scope="col" className="py-1 pr-3">What it was</th>
                  <th scope="col" className="py-1">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...(matched.data?.lines ?? []), ...(ignored.data?.lines ?? [])].map((line) => (
                  <Matched key={line.id} line={line} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default HelmBank;
