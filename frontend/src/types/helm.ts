/** Helm, the flagship engine: the shapes the API returns (backend app/api/v1/helm.py). */

export type HelmBookKind = "WORKSPACE" | "ORGANIZATION" | "SAMPLE";
export type HelmRole = "OWNER" | "ADMIN" | "BILLING";
export type AutonomyMode = "watch" | "propose" | "act";
export type DriftState = "holding" | "drifting" | "breached" | "unknown";
export type CheckResult = "pass" | "fail" | "needs_approval" | "not_applicable";
export type ActionState =
  | "PROPOSED"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTED"
  | "REJECTED"
  | "BLOCKED"
  | "UNDONE"
  | "SUPERSEDED"
  | "FAILED";
export type ActionKind =
  | "hold_payment"
  | "release_hold"
  | "schedule_payment"
  | "approve_payment"
  | "mark_paid"
  | "draft_dispute"
  | "draft_reconciliation"
  | "send_reminder"
  | "request_document"
  | "create_task"
  | "decide_renewal"
  | "notify"
  | "webhook";

export interface HelmAccess {
  helm: boolean;
  preview: boolean;
  custom: boolean;
  approval_chains: boolean;
  all_workspaces: boolean;
  api: boolean;
}

export interface HelmBook {
  id: string;
  kind: HelmBookKind;
  name: string;
  workspace_id: string | null;
  workspace_name: string | null;
  readable: boolean;
  sample: boolean;
  read_only: boolean;
  first_run: { step: number; completed_at: string | null };
  plan: { id: string; version: number; created_at: string; status: string } | null;
  created_at: string | null;
}

export interface HelmWorkspaceOption {
  id: string;
  name: string;
  slug: string;
  is_sample: boolean;
  has_book: boolean;
  currency: string;
}

export interface HelmSampleState {
  workspace_id: string | null;
  book_id: string | null;
  building: boolean;
  name: string;
}

export interface HelmOverview {
  sample: HelmSampleState;
  access: HelmAccess;
  role: HelmRole;
  can_decide: boolean;
  limits: { mandates: number | null; active_mandates: number; backtest_months: number };
  preview: { backtest_allowed: boolean; next_backtest_at: string | null };
  books: HelmBook[];
  workspaces: HelmWorkspaceOption[];
}

export interface GuardCheck {
  id: string;
  title: string;
  result: CheckResult;
  detail: string;
  evidence?: Record<string, unknown>;
}

export interface HelmApproval {
  role: string | null;
  at: string | null;
  by_helm: boolean;
  by_you: boolean;
}

export interface HelmAction {
  id: string;
  kind: ActionKind;
  state: ActionState;
  autonomy: AutonomyMode;
  needs_approval: boolean;
  guard_passed: boolean;
  /** How many different people approve it: 1, or 2 or 3 on an approval chain (Enterprise). */
  required_approvals: number;
  /** Who approved so far: roles only, never names; `by_you` marks the viewer's own approval. */
  approvals: HelmApproval[];
  /** The approvals that count towards the chain (a person's, from a role the chain takes). */
  approvals_given: number;
  /** The roles whose approvals count on this payment's chain; null: anyone who may approve it. */
  approver_roles: HelmRole[] | null;
  mandate_id: string | null;
  mandate_version: number | null;
  workspace_id: string | null;
  workspace_name: string | null;
  run_date: string | null;
  currency: string | null;
  undoable: boolean;
  irreversible: boolean;
  certificate_id: string | null;
  rejected_reason: string | null;
  created_at: string | null;
  executed_at: string | null;
  readable: boolean;
  title: string;
  explanation: string;
  amount_micros: number | null;
  value_micros: number | null;
  work_item_id: string | null;
  promise_id: string | null;
  counterparty_key: string | null;
  params: Record<string, unknown>;
  guard: { passed?: boolean; needs_approval?: boolean; checks?: GuardCheck[] };
  result: Record<string, unknown>;
  ask_access?: boolean;
}

export interface ReadbackPart {
  text?: string;
  chip?: string;
  value?: unknown;
  display?: string;
  kind?: string;
}

export interface Readback {
  sentence: string;
  parts: ReadbackPart[];
}

export interface Instrument {
  mandate_id: string;
  version: number;
  template: string;
  title: string;
  kind: string;
  mode: AutonomyMode;
  state: DriftState;
  figure: string;
  detail: string;
  figure_value: number | null;
  items: Record<string, unknown>[];
}

export interface CashPoint {
  on: string;
  p10: number;
  p50: number;
  p90: number;
}

export interface SimulationSummary {
  seed: number;
  futures: number;
  cash_paths: Record<string, CashPoint[]>;
  floor_probability: Record<string, number>;
  expected_invoices: {
    counterparty_key: string;
    counterparty: string;
    expected_on: string;
    currency: string;
    amount_micros: number;
    missing: boolean;
  }[];
  duration_ms: number;
}

export interface VisibilityPart {
  seen: number;
  share: number | null;
  [key: string]: number | null;
}

export interface PlanSummary {
  instruments?: Instrument[];
  counts?: { actions: number; payments: number; needs_approval: number; blocked: number };
  payments_by_currency?: Record<string, number>;
  optimizer?: {
    status: string;
    solver: string;
    discounts_micros: number;
    late_penalties_micros: number;
    early_cost_micros: number;
    deferred: string[];
    duration_ms: number;
  };
  simulation?: SimulationSummary | null;
  forecast?: SimulationSummary | null;
  cash?: Record<string, { amount_micros: number; as_of: string; source: string; age_days: number }>;
  visibility?: {
    payables?: VisibilityPart;
    suppliers?: VisibilityPart;
    contracts?: VisibilityPart;
    overall?: number | null;
  };
  risks?: HelmRisk[];
  run_days?: string[];
  as_of?: string;
}

export interface HelmRisk {
  mandate: string;
  on: string | null;
  summary: string;
  probability: number | null;
  currency: string | null;
  amount_micros: number | null;
}

export interface ConflictReport {
  headline: string;
  shortfalls?: { currency: string; on: string; amount_micros: number }[];
  relaxed?: string[];
  options: string[];
  late_items?: string[];
}

export interface HelmPlan {
  id: string;
  version: number;
  status: "OPTIMAL" | "APPROXIMATE" | "CONFLICT" | "EMPTY";
  created_at: string;
  seed: string;
  inputs_digest: string;
  horizon_days: number;
  conflict: ConflictReport | null;
  duration_ms: number | null;
  triggered_by: string;
  mandate_versions: { mandate_id: string; version: number; template: string }[];
}

export interface ValueHeld {
  total: Record<string, number>;
  by_category: Record<string, Record<string, number>>;
  by_month: Record<string, Record<string, number>>;
  events: number;
  rule: string;
}

export interface HelmMandate {
  id: string;
  book_id: string;
  template: string;
  title: string;
  status: "DRAFT" | "ACTIVE" | "PAUSED" | "ARCHIVED";
  version: number;
  spec: Record<string, unknown> | null;
  readback: Readback | null;
  kind: string | null;
  group: string | null;
  is_limit: boolean;
  law_links: { title: string; url: string }[];
  disclaimer: string | null;
  activated_at: string | null;
  created_at: string | null;
}

export interface HelmCockpit {
  book: HelmBook;
  plan: HelmPlan | null;
  summary: PlanSummary;
  actions: HelmAction[];
  recent: HelmAction[];
  value_held: ValueHeld | null;
  mandates: HelmMandate[];
  /** mandate id -> its key figure, one point a day for the last 30 days, oldest first. */
  trends?: Record<string, TrendPoint[]>;
}

export interface TrendPoint {
  on: string;
  value: number;
  state: DriftState | null;
}

/** A what-if (POST …/books/{id}/what-if): leave out what does not change. */
export interface HelmWhatIfInput {
  /** A one-off change to today's cash, signed, in millionths of the currency. */
  cash_change_micros?: number;
  /** The cash change's currency; may be left out when the book holds cash in one currency. */
  currency?: string;
  /** Customers pay this many days later than their own history says (0 to 90). */
  delay_receivables_days?: number;
  /** Invoices we owe (promise ids) to hold out of the plan. */
  hold_promise_ids?: string[];
  /** How far ahead to compare (7 to 90 days; 90 when left out, like the flight path). */
  horizon_days?: number;
}

/** One side of a what-if: today's plan ("baseline") or the scenario's. */
export interface HelmWhatIfSide {
  cash_paths: Record<string, CashPoint[]>;
  floor_probability: Record<string, number>;
  lowest: Record<string, { on: string; p10: number } | null>;
  end: Record<string, CashPoint>;
  cash_today: Record<string, number>;
  status: HelmPlan["status"];
  conflict: { headline: string; options: string[] } | null;
  payments: number;
  payments_by_currency: Record<string, number>;
  deferred: number;
}

export interface HelmWhatIfChange {
  mandate_id: string;
  title: string;
  template: string;
  baseline: { state: DriftState; figure: string; detail: string };
  scenario: { state: DriftState; figure: string; detail: string };
}

export interface HelmWhatIf {
  as_of: string;
  horizon_days: number;
  futures: number;
  seed: string;
  inputs: {
    cash_change_micros: number;
    currency: string | null;
    delay_receivables_days: number;
    hold_promise_ids: string[];
    horizon_days: number;
  };
  /** currency -> the highest active cash floor */
  floors: Record<string, number>;
  baseline: HelmWhatIfSide;
  scenario: HelmWhatIfSide;
  /** Only the mandates whose state changes. */
  mandates: HelmWhatIfChange[];
  summary: string;
  approximate: boolean;
  readable: boolean;
  saved: false;
  note: string;
}

export interface LibraryTemplate {
  key: string;
  title: string;
  group: string;
  order: number;
  kind: string;
  summary: string;
  plan: "business" | "enterprise";
  available: boolean;
  defaults: Record<string, unknown>;
  readback: Readback;
  is_limit: boolean;
  law_links: { title: string; url: string }[];
  disclaimer: string | null;
  recommended_for: string[];
  /** Parameters with no default: the value shown is an example the person must set before adding. */
  required: string[];
  /** Several mandates may use it, each with its own rule (the custom condition): never "Already yours". */
  repeatable: boolean;
  /** The custom condition's rows editor: the facts it may test, their comparisons and the outcomes. */
  editor?: ConditionEditorSpec;
}

/** A comparison a custom condition makes (backend mandates/library/custom_condition.py OP_WORDS). */
export type ConditionOp = ">" | ">=" | "<" | "<=" | "=" | "!=" | "contains" | "in";

export interface ConditionField {
  key: string;
  label: string;
  type: "money" | "number" | "text" | "bool" | "choice";
  ops: { op: ConditionOp; words: string }[];
  unit: string | null;
  choices: { value: string; label: string }[];
}

export interface ConditionEditorSpec {
  fields: ConditionField[];
  matches: { value: "all" | "any"; label: string }[];
  outcomes: { value: "hold" | "approval" | "notify"; label: string; help: string }[];
  max_conditions: number;
}

/** One stored condition: `field op value` (money as {amount_micros, currency}; "in" as a list). */
export interface CustomCondition {
  field: string;
  op: ConditionOp;
  value: unknown;
}

export interface HelmLibrary {
  groups: string[];
  templates: LibraryTemplate[];
  /** First run: each kind of business in plain words, with the mandates recommended for it. */
  business_types: { key: string; label: string; recommended: string[] }[];
}

export interface MandatePreview {
  valid: boolean;
  problems: string[];
  spec: Record<string, unknown> | null;
  readback: Readback | null;
  /** A drafted spec's required parameters the sentence did not state (the example shown is not theirs). */
  unstated?: string[];
}

export interface DraftCard {
  template: string;
  title: string;
  summary: string;
  group: string;
}

/** POST …/mandates/draft: a sentence read by the model and checked by the validator (brief §5.3). */
export interface HelmDraft {
  sentence: string;
  source: "model" | "gallery";
  reason: "NO_MODEL" | "MODEL_UNAVAILABLE" | "NOT_UNDERSTOOD" | "NO_TEMPLATE" | null;
  message: string | null;
  draft: MandatePreview | null;
  template: DraftCard | null;
  alternatives: DraftCard[];
  unknown_counterparties: string[];
  backtest: { months: number } | null;
}

export interface HelmIdentity {
  name: string | null;
  tax_id: string | null;
  tax_kind?: string | null;
  source?: string;
  evidence?: string;
}

export interface HelmSettings {
  identities: HelmIdentity[];
  identities_confirmed_at: string | null;
  run_weekdays: number[];
  horizon_days: number;
  cost_of_capital_pct: number;
  default_autonomy: AutonomyMode;
  briefing_enabled: boolean;
  business_type: string | null;
  first_run_step: number;
  approval_chain: HelmApprovalChain;
  cash: { currency: string; amount_micros: number; as_of: string; source: string; age_days: number }[];
}

/** One tier of an approval chain: above this amount, this many different people approve a payment. */
export interface ApprovalChainTier {
  above_micros: number;
  currency: string;
  approvals: 2 | 3;
  /** Whose approvals count; null: owners, admins and the billing role. */
  roles: HelmRole[] | null;
}

export interface HelmApprovalChain {
  /** The plan carries approval chains (Enterprise); a kept chain applies only then. */
  available: boolean;
  tiers: ApprovalChainTier[];
  /** Each tier as a sentence: "Above ₹10 lakh: two approvals from different people (owners or admins)." */
  sentences: string[];
  updated_at: string | null;
  /** Tiers the organization has too few people to meet, in words. */
  warnings: string[];
}

export interface ApproveAllResult {
  approved: number;
  /** Approved by you, and now waiting for another person on an approval chain. */
  waiting: number;
  skipped: number;
  /** Approved or rejected a moment before, from another tab or by another person (only when there were any). */
  already_decided?: number;
}

export interface HelmCounterparty {
  id: string;
  key: string;
  name: string;
  role: "SUPPLIER" | "CUSTOMER" | "BOTH";
  udyam_number: string | null;
  msme_category: "MICRO" | "SMALL" | "MEDIUM" | "NONE" | null;
  msme_source: string | null;
  msme_label: string;
  msme_protected: boolean;
  contact_email: string | null;
  behaviour: Record<string, unknown>;
}

export interface HelmPromise {
  id: string;
  kind: string;
  direction: string;
  status: string;
  confidence: string;
  workspace_id: string;
  workspace_name: string | null;
  readable: boolean;
  due_date: string | null;
  currency: string | null;
  title: string;
  amount_micros: number | null;
  counterparty_name: string | null;
  counterparty_key: string | null;
  work_item_id: string | null;
  window_end?: string | null;
  terms?: Record<string, unknown>;
  evidence: Record<string, unknown>[];
  fulfilments: Record<string, unknown>[];
  details: Record<string, unknown>;
  ask_access?: boolean;
}

export interface HelmLedger {
  total: number;
  promises: HelmPromise[];
  counts: Record<string, number>;
  open_totals: Record<string, Record<string, number>>;
}

/** GET …/books/{id}/calendar: money due per day, per direction and currency (readable workspaces only). */
export interface HelmCalendar {
  start: string;
  days: number;
  entries: { direction: "WE_OWE" | "OWED_TO_US"; on: string; currency: string; amount_micros: number; count: number }[];
  /** Promises in workspaces the viewer cannot read: counted, never summed. */
  hidden: { direction: "WE_OWE" | "OWED_TO_US"; on: string; count: number }[];
}

export interface PaymentRunLine {
  work_item_id: string | null;
  payee: string;
  bank_account: string | null;
  amount_micros: number | null;
  currency: string | null;
  invoice: string | null;
  state: string;
  certificate_id: string | null;
  readable: boolean;
}

export interface PaymentRun {
  days: { run_date: string; lines: PaymentRunLine[]; totals: Record<string, number> }[];
  note: string;
}

export interface HelmCertificateRow {
  id: string;
  stage: "PLAN" | "EXECUTION";
  passed: boolean;
  sha256: string;
  created_at: string;
  action_id: string | null;
  action_kind: string | null;
  title: string;
  readable: boolean;
}

export interface HelmCertificate {
  id: string;
  stage: "PLAN" | "EXECUTION";
  passed: boolean;
  sha256: string;
  verified: boolean;
  created_at: string;
  action_id: string | null;
  readable: boolean;
  workspace_name: string | null;
  body: Record<string, unknown> & {
    action?: Record<string, unknown>;
    checks?: GuardCheck[];
    mandates?: { mandate_id: string; version: number; template: string; spec_hash: string }[];
    facts?: Record<string, unknown>;
    snapshot?: { digest: string; as_of: string };
    issued_at?: string;
    engine_version?: string;
  };
  canonical: string | null;
}

export type Honesty = "confirmed" | "likely" | "potential";

export interface BacktestFinding {
  mandate: string;
  on: string;
  action: string;
  summary: string;
  honesty: Honesty;
  category: string;
  currency: string | null;
  value_micros: number;
  evidence: Record<string, unknown>[];
}

export interface BacktestMandateResult {
  template: string;
  title: string;
  count: number;
  totals: Record<Honesty, Record<string, number>>;
  findings: BacktestFinding[];
  truncated: boolean;
}

export interface BacktestResult {
  period?: { start: string; end: string };
  mandates?: BacktestMandateResult[];
  totals?: Record<Honesty, Record<string, number>>;
  by_category?: Record<string, Record<Honesty, Record<string, number>>>;
  counts?: Record<string, number>;
  timeline?: { on: string; events: { mandate: string; summary: string; honesty: Honesty; category: string }[] }[];
  cash_history?: Record<string, { on: string; amount_micros: number; source: string }[]>;
  honesty_rule?: string;
  duration_ms?: number;
}

export interface HelmBacktest {
  id: string;
  book_id: string;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
  months: number;
  period_start: string;
  period_end: string;
  preview: boolean;
  summary: string | null;
  error: string | null;
  mandates: { template: string; title: string | null }[];
  created_at: string | null;
  finished_at: string | null;
  result: BacktestResult | null;
  /** A finished replay's PDF: its file name, and the SHA-256 of the stored result its footer prints. */
  report?: { filename: string; result_sha256: string } | null;
}

// ---------------------------------------------------------------------------
// Bank statements (owner addition A)
// ---------------------------------------------------------------------------

export interface BankMapping {
  date: string | null;
  description?: string | null;
  reference?: string | null;
  amount?: string | null;
  debit?: string | null;
  credit?: string | null;
  drcr?: string | null;
  balance?: string | null;
  /** A Dr/Cr column for the balance itself (overdraft accounts): "Dr" means overdrawn. */
  balance_drcr?: string | null;
  date_format: "DMY" | "MDY" | "YMD";
  outflow_negative?: boolean;
}

export interface HelmBankAccount {
  id: string;
  name: string;
  last4: string | null;
  label: string;
  currency: string;
  mapping: BankMapping | null;
  waiting: number;
  balance: { amount_micros: number; as_of: string } | null;
}

export interface BankSampleLine {
  line_no: number;
  on: string;
  description: string;
  reference: string;
  amount_micros: number;
  balance_micros: number | null;
}

export interface BankPreview {
  columns: string[];
  header_row: number;
  rows: number;
  raw: string[][];
  mapping: BankMapping;
  saved: boolean;
  sample: BankSampleLine[];
  problems: { line_no: number | null; problem: string }[];
}

export interface BankImport {
  id: string;
  account_id: string;
  filename: string;
  lines_read: number;
  lines_new: number;
  lines_skipped: number;
  lines_matched: number;
  lines_waiting: number;
  problems: { line_no: number | null; problem: string }[];
  period_start: string | null;
  period_end: string | null;
  closing_balance_micros: number | null;
  closing_on: string | null;
  created_at: string | null;
}

export interface BankSuggestion {
  work_item_id: string | null;
  workspace_id?: string | null;
  kind: "PAYABLE" | "RECEIVABLE";
  document: string | null;
  number: string | null;
  counterparty: string | null;
  amount_micros: number;
  currency: string | null;
  reasons: string[];
  held: boolean;
  readable?: boolean;
}

export type BankLineStatus = "UNMATCHED" | "SUGGESTED" | "MATCHED" | "IGNORED";

export interface BankLine {
  id: string;
  account: string | null;
  account_id: string;
  import_id: string;
  line_no: number;
  on: string;
  description: string;
  reference: string;
  amount_micros: number;
  currency: string;
  balance_micros: number | null;
  status: BankLineStatus;
  work_item_id: string | null;
  workspace_id: string | null;
  readable: boolean;
  match: {
    by?: "HELM" | "PERSON";
    kind?: "PAYABLE" | "RECEIVABLE";
    document?: string | null;
    number?: string | null;
    counterparty?: string | null;
    reasons?: string[];
    suggestions?: BankSuggestion[];
    warning?: string | null;
  };
  note: string | null;
  action: { id: string; state: ActionState; certificate_id: string | null } | null;
  decided_at: string | null;
}

export interface BankLines {
  lines: BankLine[];
  counts: Record<BankLineStatus, number>;
}
