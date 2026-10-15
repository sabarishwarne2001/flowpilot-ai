/** Helm, the flagship engine. Organization-scoped (backend app/api/v1/helm.py). */
import { apiClient } from "@/services/api/client";
import type {
  HelmAction,
  HelmBacktest,
  HelmSampleState,
  HelmBook,
  HelmCertificate,
  HelmCertificateRow,
  HelmCockpit,
  HelmCounterparty,
  HelmLedger,
  HelmLibrary,
  HelmMandate,
  HelmOverview,
  HelmSettings,
  MandatePreview,
  PaymentRun,
} from "@/types/helm";

const base = (organizationId: string): string => `/organizations/${encodeURIComponent(organizationId)}/helm`;
const book = (organizationId: string, bookId: string): string =>
  `${base(organizationId)}/books/${encodeURIComponent(bookId)}`;

export const helmKeys = {
  all: (organizationId: string) => ["helm", organizationId] as const,
  overview: (organizationId: string) => ["helm", organizationId, "overview"] as const,
  library: (organizationId: string) => ["helm", organizationId, "library"] as const,
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
  certificates: (organizationId: string, bookId: string) =>
    ["helm", organizationId, "book", bookId, "certificates"] as const,
  certificate: (organizationId: string, certificateId: string) =>
    ["helm", organizationId, "certificate", certificateId] as const,
  action: (organizationId: string, actionId: string) => ["helm", organizationId, "action", actionId] as const,
  backtests: (organizationId: string, bookId: string) => ["helm", organizationId, "book", bookId, "backtests"] as const,
  backtest: (organizationId: string, backtestId: string) => ["helm", organizationId, "backtest", backtestId] as const,
};

export const getHelmOverview = async (organizationId: string): Promise<HelmOverview> =>
  (await apiClient.get<HelmOverview>(base(organizationId))).data;

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

export const approveAllHelmActions = async (
  organizationId: string,
  bookId: string,
): Promise<{ approved: number; skipped: number }> =>
  (await apiClient.post<{ approved: number; skipped: number }>(`${book(organizationId, bookId)}/plan/approve-all`))
    .data;

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
  body: MandateBody,
): Promise<MandatePreview> =>
  (await apiClient.post<MandatePreview>(`${book(organizationId, bookId)}/mandates/preview`, body)).data;

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
  body: Partial<Omit<HelmSettings, "cash" | "identities_confirmed_at">> & {
    identities?: { name: string | null; tax_id: string | null }[];
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
  } = {},
): Promise<HelmLedger> =>
  (
    await apiClient.get<HelmLedger>(`${book(organizationId, bookId)}/ledger`, {
      params: Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined && value !== "")),
    })
  ).data;

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
  body: { book_id?: string; workspace_id?: string; months: number; templates?: string[] },
): Promise<HelmBacktest> => (await apiClient.post<HelmBacktest>(`${base(organizationId)}/backtests`, body)).data;

export const listHelmBacktests = async (organizationId: string, bookId: string): Promise<HelmBacktest[]> =>
  (await apiClient.get<{ backtests: HelmBacktest[] }>(`${book(organizationId, bookId)}/backtests`)).data.backtests;

export const getHelmBacktest = async (organizationId: string, backtestId: string): Promise<HelmBacktest> =>
  (await apiClient.get<HelmBacktest>(`${base(organizationId)}/backtests/${encodeURIComponent(backtestId)}`)).data;
