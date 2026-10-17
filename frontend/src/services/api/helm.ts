/** Helm, the flagship engine. Organization-scoped (backend app/api/v1/helm.py). */
import { apiClient } from "@/services/api/client";
import { ApiError, parseErrorEnvelope } from "@/services/api/errors";
import type {
  ApprovalChainTier,
  ApproveAllResult,
  HelmAction,
  HelmBacktest,
  HelmSampleState,
  HelmBook,
  HelmCertificate,
  HelmCertificateRow,
  HelmCockpit,
  HelmCounterparty,
  HelmCalendar,
  HelmLedger,
  HelmLibrary,
  HelmMandate,
  HelmOverview,
  HelmSettings,
  MandatePreview,
  BankImport,
  BankLine,
  BankLines,
  BankMapping,
  BankPreview,
  HelmBankAccount,
  HelmDraft,
  HelmWhatIf,
  HelmWhatIfInput,
  PaymentRun,
} from "@/types/helm";

const base = (organizationId: string): string => `/organizations/${encodeURIComponent(organizationId)}/helm`;
const book = (organizationId: string, bookId: string): string =>
  `${base(organizationId)}/books/${encodeURIComponent(bookId)}`;

export const helmKeys = {
  all: (organizationId: string) => ["helm", organizationId] as const,
  overview: (organizationId: string) => ["helm", organizationId, "overview"] as const,
  library: (organizationId: string) => ["helm", organizationId, "library"] as const,
  workspaceCard: (organizationId: string, workspaceId: string) =>
    ["helm", organizationId, "workspace-card", workspaceId] as const,
  book: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId] as const,
  cockpit: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "cockpit"] as const,
  mandates: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "mandates"] as const,
  settings: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "settings"] as const,
  counterparties: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "counterparties"] as const,
  ledger: (organizationId: string, bookId: string, filters: Record<string, string | boolean | undefined>) =>
    ["helm", organizationId, "book", bookId, "ledger", filters] as const,
  paymentRun: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "payment-run"] as const,
  calendar: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "calendar"] as const,
  certificates: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "certificates"] as const,
  certificate: (organizationId: string, certificateId: string) =>
    ["helm", organizationId, "certificate", certificateId] as const,
  action: (organizationId: string, actionId: string) => ["helm", organizationId, "action", actionId] as const,
  backtests: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "backtests"] as const,
  backtest: (organizationId: string, backtestId: string) => ["helm", organizationId, "backtest", backtestId] as const,
  bankAccounts: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "bank-accounts"] as const,
  bankLines: (organizationId: string, bookId: string, status: string) =>
    ["helm", organizationId, "book", bookId, "bank-lines", status] as const,
  bankImports: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "bank-imports"] as const,
};

export const getHelmOverview = async (organizationId: string): Promise<HelmOverview> =>
  (await apiClient.get<HelmOverview>(base(organizationId))).data;

export interface HelmWorkspaceCard {
  state: "active" | "available" | "preview";
  book_id: string | null;
  value_held: Record<string, number>;
  waiting: number;
}

export const getHelmWorkspaceCard = async (organizationId: string, workspaceId: string): Promise<HelmWorkspaceCard> =>
  (await apiClient.get<HelmWorkspaceCard>(`${base(organizationId)}/workspaces/${encodeURIComponent(workspaceId)}/card`)).data;

export const getHelmLibrary = async (organizationId: string): Promise<HelmLibrary> =>
  (await apiClient.get<HelmLibrary>(`${base(organizationId)}/library`)).data;

export const createHelmBook = async (
  organizationId: string,
  body: { workspace_id?: string | null; kind?: "WORKSPACE" | "ORGANIZATION"; name?: string },
): Promise<HelmBook> => (await apiClient.post<HelmBook>(`${base(organizationId)}/books`, body)).data;

export const getHelmCockpit = async (organizationId: string, bookId: string): Promise<HelmCockpit> =>
  (await apiClient.get<HelmCockpit>(`${book(organizationId, bookId)}/cockpit`)).data;

export const replanHelmBook = async (organizationId: string, bookId: string): Promise<HelmCockpit> =>
  (await apiClient.post<HelmCockpit>(`${book(organizationId, bookId)}/plan`)).data;

/** Today's plan beside a scenario's (cash change, customers paying later, invoices held). Saves nothing. */
export const runHelmWhatIf = async (organizationId: string, bookId: string, body: HelmWhatIfInput): Promise<HelmWhatIf> =>
  (await apiClient.post<HelmWhatIf>(`${book(organizationId, bookId)}/what-if`, body)).data;

/** Approve everything that passed the Guard; on an approval chain some wait for another person. */
export const approveAllHelmActions = async (organizationId: string, bookId: string): Promise<ApproveAllResult> =>
  (await apiClient.post<ApproveAllResult>(`${book(organizationId, bookId)}/plan/approve-all`)).data;

export const getHelmAction = async (organizationId: string, actionId: string): Promise<HelmAction> =>
  (await apiClient.get<HelmAction>(`${base(organizationId)}/actions/${encodeURIComponent(actionId)}`)).data;

export const approveHelmAction = async (organizationId: string, actionId: string): Promise<HelmAction> =>
  (await apiClient.post<HelmAction>(`${base(organizationId)}/actions/${encodeURIComponent(actionId)}/approve`)).data;

export const rejectHelmAction = async (organizationId: string, actionId: string, reason: string): Promise<HelmAction> =>
  (
    await apiClient.post<HelmAction>(`${base(organizationId)}/actions/${encodeURIComponent(actionId)}/reject`, {
      reason,
    })
  ).data;

export const undoHelmAction = async (organizationId: string, actionId: string): Promise<HelmAction> =>
  (await apiClient.post<HelmAction>(`${base(organizationId)}/actions/${encodeURIComponent(actionId)}/undo`)).data;

export const listHelmMandates = async (organizationId: string, bookId: string): Promise<HelmMandate[]> =>
  (await apiClient.get<{ mandates: HelmMandate[] }>(`${book(organizationId, bookId)}/mandates`)).data.mandates;

export interface MandateBody {
  template: string;
  params?: Record<string, unknown> | null;
  scope?: Record<string, unknown> | null;
  autonomy?: Record<string, unknown> | null;
  title?: string | null;
  activate?: boolean;
}

export const createHelmMandate = async (
  organizationId: string,
  bookId: string,
  body: MandateBody,
): Promise<HelmMandate> => (await apiClient.post<HelmMandate>(`${book(organizationId, bookId)}/mandates`, body)).data;

export const previewHelmMandate = async (
  organizationId: string,
  bookId: string,
  body: MandateBody & { mandate_id?: string },
): Promise<MandatePreview> =>
  (await apiClient.post<MandatePreview>(`${book(organizationId, bookId)}/mandates/preview`, body)).data;

export const draftHelmMandate = async (organizationId: string, bookId: string, sentence: string): Promise<HelmDraft> =>
  (await apiClient.post<HelmDraft>(`${book(organizationId, bookId)}/mandates/draft`, { sentence })).data;

export const updateHelmMandate = async (
  organizationId: string,
  bookId: string,
  mandateId: string,
  body: Omit<MandateBody, "template" | "activate">,
): Promise<HelmMandate> =>
  (
    await apiClient.patch<HelmMandate>(
      `${book(organizationId, bookId)}/mandates/${encodeURIComponent(mandateId)}`,
      body,
    )
  ).data;

export const setHelmMandateStatus = async (
  organizationId: string,
  bookId: string,
  mandateId: string,
  verb: "activate" | "pause" | "archive",
): Promise<HelmMandate> =>
  (
    await apiClient.post<HelmMandate>(
      `${book(organizationId, bookId)}/mandates/${encodeURIComponent(mandateId)}/${verb}`,
    )
  ).data;

export const getHelmSettings = async (organizationId: string, bookId: string): Promise<HelmSettings> =>
  (await apiClient.get<HelmSettings>(`${book(organizationId, bookId)}/settings`)).data;

export const updateHelmSettings = async (
  organizationId: string,
  bookId: string,
  body: Partial<Omit<HelmSettings, "cash" | "identities_confirmed_at" | "approval_chain">> & {
    identities?: { name: string | null; tax_id: string | null }[];
    /** Enterprise: the approval chain's tiers; an empty list removes it. */
    approval_chain?: { tiers: ApprovalChainTier[] };
  },
): Promise<HelmSettings> => (await apiClient.patch<HelmSettings>(`${book(organizationId, bookId)}/settings`, body)).data;

export const addHelmCashPosition = async (
  organizationId: string,
  bookId: string,
  body: { currency: string; amount: string; as_of: string; note?: string },
): Promise<unknown> => (await apiClient.post(`${book(organizationId, bookId)}/cash-positions`, body)).data;

export const listHelmCounterparties = async (organizationId: string, bookId: string): Promise<HelmCounterparty[]> =>
  (await apiClient.get<{ counterparties: HelmCounterparty[] }>(`${book(organizationId, bookId)}/counterparties`)).data
    .counterparties;

export const updateHelmCounterparty = async (
  organizationId: string,
  bookId: string,
  counterpartyId: string,
  body: { msme_category?: string | null; contact_email?: string | null; contact_name?: string | null },
): Promise<HelmCounterparty> =>
  (
    await apiClient.patch<HelmCounterparty>(
      `${book(organizationId, bookId)}/counterparties/${encodeURIComponent(counterpartyId)}`,
      body,
    )
  ).data;

export const getHelmLedger = async (
  organizationId: string,
  bookId: string,
  filters: {
    kind?: string | undefined;
    direction?: string | undefined;
    status?: string | undefined;
    counterparty?: string | undefined;
    at_risk?: boolean | undefined;
    due_on?: string | undefined;
  } = {},
): Promise<HelmLedger> =>
  (
    await apiClient.get<HelmLedger>(`${book(organizationId, bookId)}/ledger`, {
      params: Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined && value !== "")),
    })
  ).data;

/** The cockpit's Promise calendar: money due per day for the coming weeks (read-only). */
export const getHelmCalendar = async (organizationId: string, bookId: string, days = 84): Promise<HelmCalendar> =>
  (await apiClient.get<HelmCalendar>(`${book(organizationId, bookId)}/calendar`, { params: { days } })).data;

export const getHelmPaymentRun = async (organizationId: string, bookId: string): Promise<PaymentRun> =>
  (await apiClient.get<PaymentRun>(`${book(organizationId, bookId)}/payment-run`)).data;

export const markHelmInvoicePaid = async (
  organizationId: string,
  bookId: string,
  workItemId: string,
  body: { paid_on: string; reference?: string | null },
): Promise<HelmAction> =>
  (
    await apiClient.post<HelmAction>(
      `${book(organizationId, bookId)}/invoices/${encodeURIComponent(workItemId)}/paid`,
      body,
    )
  ).data;

export const listHelmCertificates = async (organizationId: string, bookId: string): Promise<HelmCertificateRow[]> =>
  (await apiClient.get<{ certificates: HelmCertificateRow[] }>(`${book(organizationId, bookId)}/certificates`)).data
    .certificates;

export const getHelmCertificate = async (organizationId: string, certificateId: string): Promise<HelmCertificate> =>
  (await apiClient.get<HelmCertificate>(`${base(organizationId)}/certificates/${encodeURIComponent(certificateId)}`))
    .data;

export const downloadHelmCertificate = async (organizationId: string, certificateId: string): Promise<Blob> =>
  (
    await apiClient.get<Blob>(`${base(organizationId)}/certificates/${encodeURIComponent(certificateId)}/download`, {
      responseType: "blob",
    })
  ).data;

export const buildHelmSample = async (organizationId: string): Promise<HelmSampleState> =>
  (await apiClient.post<HelmSampleState>(`${base(organizationId)}/sample`)).data;

export const deleteHelmSample = async (organizationId: string): Promise<HelmSampleState> =>
  (await apiClient.delete<HelmSampleState>(`${base(organizationId)}/sample`)).data;

export const requestHelmBacktest = async (
  organizationId: string,
  body: {
    book_id?: string;
    workspace_id?: string;
    months: number;
    templates?: string[];
    /** Full draft specs: a drafted mandate replayed with its own numbers before it is saved. */
    mandates?: { template: string; params?: Record<string, unknown> | null; scope?: Record<string, unknown> | null }[];
  },
): Promise<HelmBacktest> => (await apiClient.post<HelmBacktest>(`${base(organizationId)}/backtests`, body)).data;

export const listHelmBacktests = async (organizationId: string, bookId: string): Promise<HelmBacktest[]> =>
  (await apiClient.get<{ backtests: HelmBacktest[] }>(`${book(organizationId, bookId)}/backtests`)).data.backtests;

export const getHelmBacktest = async (organizationId: string, backtestId: string): Promise<HelmBacktest> =>
  (await apiClient.get<HelmBacktest>(`${base(organizationId)}/backtests/${encodeURIComponent(backtestId)}`)).data;

/**
 * A finished replay as a PDF report. A refusal in words (409: still running or failed; 404: gone)
 * arrives as a Blob, which the client's error reader cannot see into, so it is read here and thrown
 * as the server's own sentence. Every other status goes through the client as usual.
 */
export const downloadHelmBacktestPdf = async (organizationId: string, backtestId: string): Promise<Blob> => {
  const response = await apiClient.get<Blob>(
    `${base(organizationId)}/backtests/${encodeURIComponent(backtestId)}/pdf`,
    {
      responseType: "blob",
      timeout: 120_000,
      validateStatus: (status) => (status >= 200 && status < 300) || status === 404 || status === 409,
    },
  );
  if (response.status >= 300) {
    let body: unknown = null;
    try {
      body = JSON.parse(await response.data.text()) as unknown;
    } catch {
      // not JSON: the fallback sentence below
    }
    const parsed = parseErrorEnvelope(body, response.status, "Helm could not make the PDF of this replay.");
    throw new ApiError(parsed.message, response.status, parsed.code, parsed.message, parsed.details);
  }
  return response.data;
};

// Bank statements (owner addition A). Uploads are FormData: the client sets the multipart boundary.
const UPLOAD_TIMEOUT_MS = 300_000;

export const listHelmBankAccounts = async (organizationId: string, bookId: string): Promise<HelmBankAccount[]> =>
  (await apiClient.get<{ accounts: HelmBankAccount[] }>(`${book(organizationId, bookId)}/bank-accounts`)).data.accounts;

export const createHelmBankAccount = async (
  organizationId: string,
  bookId: string,
  body: { name: string; number?: string | null; currency: string },
): Promise<HelmBankAccount> =>
  (await apiClient.post<HelmBankAccount>(`${book(organizationId, bookId)}/bank-accounts`, body)).data;

const statementForm = (file: File, fields: Record<string, string | undefined>): FormData => {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined) {
      form.append(key, value);
    }
  }
  return form;
};

export const previewHelmBankStatement = async (
  organizationId: string,
  bookId: string,
  file: File,
  accountId?: string,
  mapping?: BankMapping,
): Promise<BankPreview> =>
  (
    await apiClient.post<BankPreview>(
      `${book(organizationId, bookId)}/bank-statements/preview`,
      statementForm(file, { account_id: accountId, mapping: mapping ? JSON.stringify(mapping) : undefined }),
      { timeout: UPLOAD_TIMEOUT_MS },
    )
  ).data;

export const importHelmBankStatement = async (
  organizationId: string,
  bookId: string,
  file: File,
  accountId: string,
  mapping: BankMapping,
): Promise<BankImport> =>
  (
    await apiClient.post<BankImport>(
      `${book(organizationId, bookId)}/bank-statements`,
      statementForm(file, { account_id: accountId, mapping: JSON.stringify(mapping), save_mapping: "true" }),
      { timeout: UPLOAD_TIMEOUT_MS },
    )
  ).data;

export const listHelmBankImports = async (organizationId: string, bookId: string): Promise<BankImport[]> =>
  (await apiClient.get<{ imports: BankImport[] }>(`${book(organizationId, bookId)}/bank-imports`)).data.imports;

export const listHelmBankLines = async (organizationId: string, bookId: string, status: string): Promise<BankLines> =>
  (await apiClient.get<BankLines>(`${book(organizationId, bookId)}/bank-lines`, { params: { status } })).data;

export const matchHelmBankLine = async (organizationId: string, lineId: string, workItemId: string): Promise<BankLine> =>
  (
    await apiClient.post<BankLine>(`${base(organizationId)}/bank-lines/${encodeURIComponent(lineId)}/match`, {
      work_item_id: workItemId,
    })
  ).data;

export const ignoreHelmBankLine = async (organizationId: string, lineId: string, note: string): Promise<BankLine> =>
  (await apiClient.post<BankLine>(`${base(organizationId)}/bank-lines/${encodeURIComponent(lineId)}/ignore`, { note })).data;

export const reopenHelmBankLine = async (organizationId: string, lineId: string): Promise<BankLine> =>
  (await apiClient.post<BankLine>(`${base(organizationId)}/bank-lines/${encodeURIComponent(lineId)}/reopen`)).data;
