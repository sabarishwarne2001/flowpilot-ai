# Helm — architecture

_Status: design written at the start of the flagship session (2026-10-14) and kept current as the
build lands. Every section names the existing tables and functions Helm reads or calls, so a reader
can check the claim in the code._

## 1. What Helm is, in one paragraph

Workflows say "when X happens, do Y". Helm instead keeps the business inside a set of written
outcomes and hard limits, called **mandates** ("never pay above the agreed price", "keep cash above
₹10 lakh"). It reads every promise in the company's documents into a **Promise ledger**, measures
how far the business has drifted from each mandate, lets the **Planner** choose what to do (when to
pay whom, what to hold, whom to chase), checks every action with the **Guard** before it is proposed
and again before it runs, and lets the **Time machine** replay the past ("what would Helm have
caught?") and look ahead (the 90-day cash band). Money never moves inside Helm: paying happens in
the customer's own bank or accounting system, from a payment run list a person executes.

**The deterministic core decides; AI only assists.** Every number, check and plan comes from code
in `backend/app/services/helm/`. The only model call turns a typed sentence into a mandate draft,
which the same deterministic validator then checks. Helm works fully with AI switched off.

## 2. Books: whose business Helm is running

A **book** is one company Helm keeps inside its mandates (`helm_books`).

| Book scope | Who has it | Why |
|---|---|---|
| One workspace | Business and Enterprise | A company that uses one workspace, or an accounting firm with one workspace per client: each client is its own company, with its own cash, identities and mandates. |
| All workspaces | Enterprise only | "Planning across all workspaces" (§2.5 of the session brief). |
| The sample workspace | every plan | "Explore Helm with a sample company"; read-only on plans without `capability.helm`. |

Everything Helm owns hangs off a book: settings and identities, counterparties, cash positions,
mandates, plans, actions, certificates, backtests and value held. The Promise ledger is the one
exception: a promise comes from a document and a document belongs to one workspace, so promises are
stored per workspace and a book reads the promises of the workspaces in its scope.

## 3. Data model (migration `h1a1_helm`, after `s4b1_drop_email_settings`)

Every table carries `organization_id`; tables tied to documents carry `workspace_id` and use the
ARCH-41 composite-key pattern: `(workspace_id, organization_id) REFERENCES workspaces (id,
organization_id)` and `(work_item_id, workspace_id) REFERENCES work_items (id, workspace_id)`, so a
row that names another tenant's document is refused by PostgreSQL (23503), not by a filter someone
could forget.

| Table | What it holds | Rebuilt or source of truth |
|---|---|---|
| `helm_books` | id, organization, workspace (null = all workspaces), name, kind (WORKSPACE, ORGANIZATION, SAMPLE), preview flag | source |
| `helm_settings` | per book: our identities (names, GSTIN/VAT/EIN), payment-run weekdays, holiday calendar, horizon, cost of capital (default 12% a year), autonomy defaults, briefing opt-out, first-run state | source |
| `helm_counterparties` | per book: key (the matching engine's `vendor_key`), display name, linked entity-graph entity ids, Udyam number, MSME category and whether a person confirmed it, contact email | source (MSME and contacts) + derived (behaviour) |
| `helm_settlements` | per invoice: unpaid, scheduled, approved, paid, held, disputed; the run date, who did what, and the payment evidence | **source** (a person's decision cannot be rebuilt from documents) |
| `helm_cash_positions` | per book and currency: amount, as-of date, source (a STATEMENT document's closing balance, or typed by an admin) | source |
| `helm_promises` | the Promise ledger (§4) | **rebuildable projection** |
| `helm_fulfilments` | what kept (or broke) each promise: a payment, an invoice line, a goods receipt, a decision, a newer document | rebuildable projection |
| `helm_mandates` / `helm_mandate_versions` | a mandate and its immutable versions (spec JSON, its hash, its readback) | source |
| `helm_plans` | versioned plans: inputs digest, seed, cash bands, conflict report, mandate versions, approximate flag; an old plan is superseded, never edited | source |
| `helm_actions` | every proposed or executed action: kind, target, amount, run date, Guard result, state, approvals, idempotency key | source |
| `helm_certificates` | canonical JSON of a Guard decision and its SHA-256 | source, immutable |
| `helm_backtests` | a Time-machine run: period, mandates, findings, value by honesty class, summary | source |
| `helm_value_events` | evidence-backed value held, one row per outcome (unique `dedupe_key`, never counted twice) | source |
| `helm_tasks` | the decision and verification tasks Helm opens; they appear in the review hub as kind `HELM` | source |

`workspaces.is_sample` marks the sample workspace: it is labelled in the workspace picker, it does
not count towards `limit.workspaces`, and deleting it archives the workspace and its sample book.

## 4. The Promise ledger

### 4.1 Where each promise comes from

| Kind | Direction | Built from (existing tables) | Kept by |
|---|---|---|---|
| `PAYMENT_DUE` | we owe | INVOICE rows in `document_roles` (`vendor_key`, `document_number`, `document_date`, `currency`, `total_micros`) where we are the buyer, plus `work_items.extracted_entities` (due date, payment terms, PO reference, bank account) | a `helm_settlements` row in state PAID, with evidence |
| `PAYMENT_EXPECTED` | owed to us | INVOICE rows where **we are the seller** (our identities, §4.2) | a customer payment: a person marks it, or a STATEMENT line matches |
| `PRICE_AGREED` | owed to us | `procurement_case_lines` (PO unit price per line) from three-way matching | every invoice line at or below it (`invoice_unit_price_micros`) |
| `DELIVERY_DUE` | owed to us | PURCHASE_ORDER documents (expected delivery date) and `procurement_case_lines` | GOODS_RECEIPT documents (`receipt_quantity`) |
| `DISCOUNT_OFFERED` | option for us | the payment-terms parser (§4.3) over the invoice's or the PO's terms | paid inside the discount window (settlement evidence) |
| `NOTICE_DEADLINE` | our decision | `obligations` of kind NOTICE (ARCH-46) | a renewal decision recorded on the obligation before the deadline |
| `DOCUMENT_VALID_UNTIL` | owed to us | `obligations` of kind EXPIRY on a supplier compliance document (insurance, licence, certificate) | a newer valid document from the same supplier |
| `OBLIGATION` | either | any other ARCH-46 obligation | obligation DONE or WAIVED |

Statuses: OPEN, PARTIAL, KEPT, BROKEN, WAIVED, EXPIRED. Every promise carries its evidence (document
ids, the fields, the quote and page region where `document_evidence` has one), a confidence
inherited from the facts it came from (§4.4) and its fulfilments.

### 4.2 Our identity and counterparties

- **Our identities** (`helm_settings.identities`): legal names and tax ids. An invoice whose seller
  name or tax id matches one is a receivable. Suggested on first run from the organization's legal
  name and the most frequent **buyer** GSTIN on the book's invoices; an admin confirms them.
- **Counterparties** are keyed by the matching engine's `vendor_key`
  (`app/core/normalize.vendor_key`, the same key `document_roles` stores) and linked to entity-graph
  entities (`entities`) where ARCH-42 resolved them. Behaviour is computed from history: days late
  for customers (receivables paid against their due date), invoice cadence and amounts for
  recurring suppliers.
- **MSME:** a Udyam number (`UDYAM-XX-00-0000000`) on any of the supplier's documents marks the
  supplier as registered. A Udyam number does not say micro, small or medium, so until a person
  confirms the category (or a Udyam certificate document states it) Helm treats the supplier as
  **micro or small** (the conservative reading) and shows "category unconfirmed".

### 4.3 The payment-terms parser (`helm/terms.py`)

Deterministic. Handles "net 30", "2/10 net 30", "2% 10 days net 45", "due on receipt",
"30 days from invoice", "45 days end of month", "payable within 15 days", "immediate". Anything it
cannot read is stored as UNPARSED with the original text, and **no discount is ever assumed**.

### 4.4 Confidence

A promise inherits the weakest state of the facts it rests on:
`VERIFIED` (a person reviewed the document: `document_verifications.status = REVIEWED`, or a field
correction exists), `CALIBRATED` (verification agreed or was auto-approved under calibrated
autonomy: `AGREED` / `AUTO_APPROVED`), `EXTRACTED` (read by the model, unchecked). The Guard's fact
check (§7) lets an action run without approval only on VERIFIED or CALIBRATED facts.

### 4.5 Rebuildable projection

`helm/ledger.py` is a pure function from (documents, roles, matching lines, obligations,
settlements, identities) to promise rows, each with a deterministic `source_key`. It runs:
- **incrementally**: post-enrichment dispatch (`app/services/post_enrichment.dispatch_in_session`),
  a field correction, a review resolution and a document delete enqueue `helm.ledger_build` for that
  document (idempotency key per document and change marker); the handler rebuilds that document's
  promises and its counterparties' settlement links;
- **fully**: nightly (`helm.ledger_build` with `{"full": true}` in `DEFAULT_SCHEDULE`).
A test proves the nightly full rebuild produces exactly the rows the incremental updates did.
Reads take a shared hold on each workspace's ledger lock, so a plan never sees half a rebuild. An
invoice's payment state is the one exception to "read the projection": the snapshot takes the
`helm_settlements` row whenever its version is newer than the promise's copy, so a payment approved
seconds ago is already scheduled for the next plan and the next bank import (F-427).

## 5. Mandates

`helm/mandates/spec.py`: a typed, versioned Pydantic spec stored as JSON:
`template`, `kind` (limit "never", set point "keep within", goal "as much as possible"), `scope`
(workspaces, counterparties, currency), typed `params` (one Pydantic model per template),
`autonomy` (watch, propose, act, with `act_cap` amount and currency; default cap 0), `owner`,
`status` (draft, active, paused). Editing creates a new immutable version; plans and certificates
record the version they used.

Each library template is a module in `helm/mandates/library/` exposing `evaluate` (the drift),
`propose` (candidate actions), `guard_checks`, `backtest` and `forecast`, plus `readback` (the plain
sentence with editable chips). The validator (`helm/mandates/validator.py`) refuses impossible or
contradictory parameters in plain words.

| # | Template key | Kind | Hard in the optimiser? |
|---|---|---|---|
| 1 | `never_pay_above_agreed_price` | limit | excludes the invoice (held) |
| 2 | `never_pay_duplicate` | limit | excludes the invoice |
| 3 | `never_pay_changed_bank_account` | limit | excludes the invoice |
| 4 | `po_required_above` | limit | excludes the invoice |
| 5 | `pay_on_time` | goal (or hard, a parameter) | deadline if hard, late penalty if not |
| 6 | `capture_early_discounts` | goal | objective term |
| 7 | `keep_cash_above` | set point | cash floor constraint; simulation reports the probability |
| 8 | `msme_pay_within_limit` (India pack) | limit | hard deadline |
| 9 | `decide_renewals_before_notice` | limit | decision task |
| 10 | `supplier_documents_never_expire` | set point | document request |
| 11 | `chase_overdue_customers` | goal | reminders |
| 12 | `review_backlog_under` | set point | escalation |
| 13 | `reconcile_supplier_statements` (owner addition B, §17) | goal | a reconciliation email draft |
| — | `custom_condition` (Enterprise) | limit | `all`/`any` of `field op value` over whitelisted ledger fields |

Sentence drafting (`helm/drafting.py`): `POST …/helm/books/{id}/mandates/draft` reserves one
`assistant.message` through `plan_admission.admit_assistant_message` (402 on quota), asks the
book's model through `llm_service` with a strict JSON schema listing the templates and their
parameters, metered with `llm_metering.reserve_for_helm_draft`, and hands the answer to the same
validator. A bad, missing or refused answer falls back to the template gallery with the sentence
kept. The e2e model stand-in answers the drafting prompt for the test sentences.

## 6. The Planner (`helm/planner.py`)

Runs on a ledger change (debounced per book: the `helm.plan` job is enqueued 20 seconds later with
an idempotency key per book and minute), hourly, and on "Re-plan". If the inputs digest equals the
current plan's, the current plan is kept. One run per book at a time
(`pg_try_advisory_xact_lock` on the book).

Steps: snapshot → evaluate mandates (drift) → candidate actions → optimise payment timing →
simulate → Guard every action → persist a new plan version (the old one superseded) → notify on new
serious drift or conflict (`organization_notification_service.emit`, category `helm`).

**Payment timing** (`helm/optimizer.py`, `scipy.optimize.milp`, HiGHS): binary `x[i,d]` = pay
invoice *i* on payment-run day *d* within the horizon (default 30 days, up to 90); defer otherwise.
Each invoice at most once; held or disputed invoices excluded; hard deadlines met (MSME, and
"on time" when set hard); per currency and run day, opening cash + the simulation's P10 cumulative
inflows − payments ≥ the floor. Objective: discounts captured − late penalties − the cost of paying
early (cost of capital, default 12% a year). Time limit 5 s; on a timeout without a solution a
documented greedy heuristic (earliest hard deadline first, then discount value per rupee, cash
permitting) produces the plan, marked **approximate**. If infeasible, soft goals are relaxed in
priority order (first "on time" deadlines set hard, then the cash floor through a shortfall slack)
and a **conflict report** says, in words, what falls short, when, and the options.

**Simulation** (`helm/simulation.py`, NumPy): 1,000 seeded futures. Each customer's payment delay is
bootstrapped from their own history (a cautious default under 5 observations); each recurring
supplier's next invoice is sampled from its cadence. Outputs: P10/P50/P90 cash paths per currency,
the probability each cash set point holds under the plan, and the expected-next-invoice list (a
usual invoice that has not arrived is a drift signal). The seed is derived from the inputs digest and
stored with the plan: same inputs and seed, same plan and numbers (tested).

## 7. The Guard (`helm/guard.py`)

A pure function `guard(action, snapshot, mandates, actor, context) -> GuardResult(passed, checks)`.
Everything it needs from the database (account state, the actor's role and grants, egress decision,
whether the idempotency key already ran) is read first and passed in as data. Checks, each with an
id, a plain description, the result and its evidence:

| Check id | Passes when |
|---|---|
| `scope` | the action's document, counterparty and currency are in the mandate's scope |
| `limit:<mandate>` | every active limit that applies holds (each template's `guard_checks`) |
| `facts` | the facts the action relies on are VERIFIED or CALIBRATED; otherwise it needs approval |
| `conflicts` | no open TruthMesh conflict (`mesh_conflicts`) or corroborator discrepancy (`corroboration_runs`) on the document |
| `fraud` | no open BANK_ACCOUNT_CHANGED flag (`payment_risk_flags`) and no open duplicate finding (`anomaly_findings`, layers L0–L2) |
| `price` | every invoice line within the agreed price (§4.1 PRICE_AGREED) |
| `account` | the organization is writable (`billing_write_gate`, trial, archive) |
| `permission` | the actor may take this action (role matrix §9) and the plan has `capability.helm` |
| `destination` | egress lockdown allows the email or webhook destination (`app/core/egress.py`) |
| `idempotency` | the action key has not already run |
| `cash` | the P10 cash path stays above every active floor after this payment |
| `autonomy` | in Act mode, the amount is within the mandate's act cap; otherwise approval |

It runs at plan time and again in `helm.execute`; a check that now fails blocks the action and
re-plans. **Certificate** (`helm/certificate.py`): canonical JSON (sorted keys, no spaces, money as
integer micros) of the action, input digests, every check, the mandate versions, the snapshot hash,
the engine version and the time, plus its SHA-256; stored, audited, shown as a page and downloadable.
Bank accounts appear only masked (last 4). A seeded property test generates thousands of states and
actions and asserts no action that breaks an active limit passes.

## 8. Helm's hands (`helm/executor.py`)

| Action | Effect, through the existing service |
|---|---|
| `hold_payment` | settlement → held with the reason; notice |
| `schedule_payment` | settlement → scheduled on a run day; listed in the **Payment run** (date, payee, masked account, amount, invoice, certificate). ERP: the canonical model has no payment-date or hold field on a vendor bill (`app/services/erp/canonical.py`), so the payment run list is the hand-over (decision N-088) |
| `approve_payment` | approver action; re-runs the Guard |
| `mark_paid` | a person, or a STATEMENT line match; settlement → paid with evidence |
| `draft_dispute` | an email draft to the supplier with the evidence; sends only in Act mode with a known contact |
| `draft_reconciliation` | the supplier statement reconciliation email (§17); a draft unless Act mode with a known contact |
| `send_reminder` | receivables email at an escalation level, through `email_service` with the organization's sender settings |
| `request_document` | a public document request (`app/services/cases/requests.create`) on a "Supplier compliance documents" case for that supplier, plus the email |
| `create_task` | a `helm_tasks` row, shown in the review hub (kind `HELM`), assigned |
| `decide_renewal` | a decision task; renew, cancel or renegotiate is recorded on the obligation (`obligation_events`) |
| `notify` | `organization_notification_service.emit`, category `helm` |
| `webhook` (Enterprise) | `helm.plan.ready`, `helm.action.executed`, `helm.mandate.breached` through the outgoing-webhook service |

Approvals: in Propose mode actions wait; an approver approves one, approves the whole plan, or
rejects one with a reason. Approval is a conditional update (`PROPOSED → APPROVED`) and execution a
second one (`APPROVED → EXECUTING`) keyed by the action's unique idempotency key, so a double-click
or two tabs execute once (proven on a real uvicorn). Holds, schedules and tasks can be undone; sent
emails cannot, and the screen says so.

## 9. Who may do what

| | Owner | Admin | Billing | Member |
|---|---|---|---|---|
| See Helm (mode switch) | yes | yes | yes | — |
| Create, edit, activate, pause mandates; set act caps | yes | yes | — | — |
| Approve and reject actions | yes | yes | payment actions | — |
| Settings, identities, cash positions, counterparties | yes | yes | cash positions | — |
| Receive Helm tasks in the review hub | as today | as today | as today | as today |

Reads follow today's workspace rules (`resolve_effective_workspace_role`): an organization owner or
admin reads every workspace; a Billing member reads Helm's figures but a document's facts only in
workspaces they hold a grant for. Elsewhere the action shows its certificate with masked facts and
the workspace name, and an "Ask for access" button; approving needs read access.

## 10. Worker jobs

| Job | When | Profile |
|---|---|---|
| `helm.ledger_build` | per document change; nightly full rebuild at 02:40 UTC | LIGHT |
| `helm.plan` | debounced after a ledger change; hourly sweep; "Re-plan" | LIGHT |
| `helm.backtest` | on request (first run, the Time machine, preview once a week) | LIGHT |
| `helm.execute` | after an approval, or an Act-mode action within its cap | LIGHT |
| `helm.briefing` | hourly; sends Monday 09:00 in the organization's time zone | LIGHT |
| `helm.sample` | "Explore Helm with a sample company" (§15) | ENRICH |

All six are in `TENANT_ACTIVITY_JOB_TYPES` and `BILLING_GATED_JOB_TYPES`, gated on
`capability.helm` (preview backtests and the sample book excepted), idempotent, and visible on the
platform jobs page with the dead-letter queue.

## 11. API

Routes under `/organizations/{organization_id}/helm/…` (`app/api/v1/helm.py`), every one in the
threadpool, behind `get_organization_context` (role, billing write gate, sign-in policy) and the
capability or preview gate as its first statement. Enterprise API keys get `helm:read` and
`helm:write` (`ROUTE_SCOPE_MAP`); default deny stays.

## 12. Frontend

- **Mode switch** `Documents | Helm` in `components/layout/Header.tsx` (a radio group; last mode in
  `localStorage` inside try/catch); Owners, Admins, Billing; a lock badge below Business.
- Routes `/:orgSlug/helm/…` (`helm` is a reserved workspace slug), `HelmGuard` → `HelmLayout` with its
  own sidebar: Cockpit, Plan, Mandates, Promise ledger, Time machine, Certificates, Settings.
- Visual identity: `frontend/src/styles/helm.css` (instrument-panel tokens, one luminous accent,
  JetBrains Mono for numbers, thin rings, reduced-motion respected). Hand-built SVG flight path.

## 13. Performance budget (measured on the sandbox, 2026-10-15)

`tests/performance/test_helm_budgets.py` seeds one workspace with a distributor's year (invoices
from 40 suppliers, purchase orders, goods receipts, credit notes, monthly bank statements, one
statement of account per supplier) and times each step on real Postgres. It runs in the suite at
1,000 documents; `HELM_PERF_DOCUMENTS=10000` is the owner's run (owner addition D). The sandbox is a
4-core container, Postgres 16 in memory.

| Step | Budget | 1,000 documents | 10,000 documents |
|---|---|---|---|
| Plan for the open payables over 30 days | ≤ 10 s for 1,000 open payables | 2.9 s (88 open) | **5.8 s (1,080 open)** |
| 1,000 seeded futures of cash | ≤ 3 s | 0.01 s | 0.01 s |
| Ledger update after one document changes | ≤ 1 s | 0.7 s | **2.9 s: over budget** |
| Snapshot (every input the plan reads) | — | 0.2 s | 1.7 s |
| Every supplier statement reconciled (40) | — | 0.9 s | 1.3 s |
| Bank import with matching | — | 0.4 s (500 lines) | 4.0 s (5,000 lines) |
| First full ledger build | — | 2.8 s | 25.4 s |
| Event loop | no database work on the loop | every Helm route is a plain `def` (threadpool) | — |

Fixed on the way (F-423, F-425): the ledger compared every invoice with every statement line (now an
index by amount) and named-invoice checks scanned every known number (now one pass per line); the
reconciliation re-read every supplier's name per statement and ran twice per plan (now cached).
**Not yet within budget:** the ledger update recomputes the whole workspace on each change, which
holds the 1-second budget up to about 2,000 documents a workspace; each further 1,000 documents add
about 0.25 s. The update runs in the worker, never in a request, so nobody waits on it; making
it truly incremental (recompute only the documents a change touches) is the next step (HANDOFF).

## 14. The Time machine (`helm/backtest.py`, `helm/history.py`)

**What it answers:** "what would Helm have done over the last N months, and what would it have
held?" It never executes anything; draft mandates may be backtested.

1. `POST …/helm/backtests` checks the plan's months (preview 3, Business 6, Enterprise 24; the
   sample company may always be replayed whole) and, for Helm preview, one run a week. It stores a
   QUEUED `helm_backtests` row and queues `helm.backtest`.
2. The job rebuilds the ledger first, so the past is read from today's evidence, then builds a
   `History`: the book as of today with every promise (kept and broken), every radar finding
   whatever became of it (open, confirmed, dismissed) with the day it was raised, and every cash
   observation, oldest first. `History` is frozen data, like `Snapshot`.
3. Each mandate's pure `backtest(history, mandate)` replays the period. The day a document arrived
   is the day Helm would have seen it. Each finding names the documents it stands on.
4. Value is counted in three **honesty classes** that are never added together: **confirmed**
   (evidence shows it happened: a later credit note, a reviewer's confirmation, a second payment of
   the same bill, an evidenced payment date), **likely** (the rule fired and the facts are there,
   but nothing confirms the loss), **potential** (facts are missing). The summary is a
   deterministic sentence built from those numbers; no model writes it.
5. The result keeps per-mandate findings (up to 200 each; counts and totals cover all), totals by
   class and currency, value by category, a timeline and the cash history the flight path scrubs.

## 15. The sample company (`helm/sample_company.py`)

Meridian Wholesale Pvt Ltd, an invented Pune distributor (INR, Asia/Kolkata), so that every plan
can see Helm work on a full set of books. Nine months of purchase orders, goods receipts,
invoices, credit notes, sales invoices, contracts, supplier certificates and bank statements,
rendered as PDFs with reportlab. Planted in them: overcharges against agreed prices, one duplicate
paid twice, a bank-account change, MSME suppliers paid late, missed early-payment discounts, a
missed renewal, an expired certificate, late-paying customers and a cash dip.

- The documents are read by the platform's own engines, called directly with no plan gate: the
  role classifier, the radar's bank-account check and duplicate sweep, three-way matching and the
  obligations reader. So the sample shows what Helm does with what the platform really extracts.
- `POST …/helm/sample` queues `helm.sample` (ENRICH profile, because the duplicate sweep needs
  embeddings); `DELETE …/helm/sample` archives the workspace and its book. The sample workspace is
  marked `is_sample`, labelled, and never counts against `limit.workspaces`.
- `Planted` lists exactly what was planted; the backtest of the sample must find exactly that (a
  live test checks it to the paisa). `scripts/helm_demo_company.py --org <slug> [--rebuild]
  [--print]` builds it from the command line and refuses to run against production.

## 16. Bank statements (owner addition A; `helm/bank_parse.py`, `bank_match.py`, `bank.py`)

Migration `h1a2_helm_bank` (after `h1a1_helm`): `helm_bank_accounts` (name, last four digits only,
currency, the saved column mapping), `helm_bank_imports` (one file: its SHA-256, so the same file
twice is refused; its period and closing balance), `helm_bank_lines` (date, narration, reference,
signed amount, balance; a fingerprint per account so an overlapping period adds no line twice; the
status UNMATCHED, SUGGESTED, MATCHED or IGNORED; the invoice and the action when matched). Cash
positions gain source `BANK`, tied to the import and the account. Audit resource `HELM_BANK`.

1. **Reading** (`bank_parse`, pure): CSV (encoding and delimiter sniffed), .xlsx (openpyxl,
   read-only, formulas never evaluated) and .xls (xlrd); the header row is found below the bank's
   preamble; amounts go through the table engine's strict number reader (Western, Indian and
   European grouping; anything else is not read); dates in day-, month- or year-first order, named
   months and Excel serials; a Dr/Cr column in the bank's words. A cell that cannot be read is a
   problem on its line, never a guess. `guess_mapping` proposes the columns from the headings; the
   person confirms, and the mapping is saved per account.
2. **Importing** (`bank.import_statement`): new lines only (fingerprint = account, date, amount,
   narration, reference and how many identical lines came before it); the closing balance (when the
   file has a balance column) becomes the account's cash; the new lines and the account's earlier
   undecided ones are matched.
3. **Matching** (`bank_match`, pure): automatic only when the evidence leaves one answer (§N-095);
   otherwise up to three suggestions with their reasons, or nothing. Invoices already claimed by a
   matched line are not offered again.
4. **Recording**: a match (Helm's or a person's) is a `mark_paid` action with `bank_line_id` in its
   params, approved and queued to `helm.execute`. The executor writes the settlement PAID with
   source STATEMENT and the line as evidence; the ledger rebuild then records the payment (on time
   or late, inside a discount window or not) and value held follows from it. Undoing the action (in
   the cockpit, or "Reopen" on the line) restores the settlement and returns the line to the person.
5. **Cash** (`snapshot_store.cash_positions`): each account's latest imported balance; accounts in
   one currency are summed; the total is dated by its oldest part; a typed figure wins a tie; the
   cash-floor mandate counts imported balances as confirmed evidence.

API: `…/books/{id}/bank-accounts` (GET, POST, PATCH), `…/bank-statements/preview` and
`…/bank-statements` (multipart, 5 MB, streamed through `spool_upload_file`), `…/bank-imports`,
`…/bank-lines?status=WAITING|MATCHED|IGNORED`, `…/bank-lines/{id}/match | ignore | reopen`. Owners,
admins and billing; Helm's capability for writes. Page: `/:org/helm/bank` ("Bank statements").

## 17. Supplier statements (owner addition B; `helm/reconcile.py`, library mandate 13)

**Two kinds of STATEMENT.** The role classifier gives a bank's statement and a supplier's statement
of account the same role. Read as a bank's, a supplier's statement marked their invoices PAID (their
own statement as "evidence") and turned the balance we owe them into our cash: the defect this
addition found and fixed first (`tests/services/helm/test_supplier_statements.py`).
`ledger.statement_kind(doc, identities)` now decides, in order: a classification naming a bank; an
issuer that is a bank; an issuer that is anyone else (not us: an extractor may put the account
holder there); a bank named in the header; words only a counterparty's statement uses. A statement
nothing identifies stays a bank's, as before. Only bank statements settle invoices
(`ledger.build`) and give cash (`snapshot_store.cash_history`).

**Reconciliation** (`reconcile.reconcile`, pure). The snapshot carries every supplier statement of
the book's workspaces (`snapshot.SupplierStatement`: lines, opening and closing balance, period,
confidence; our own statements to customers are left out). Each is compared with the ledger of the
same workspace and supplier (by key, else by name): their invoices we do not have, the same invoice
at another amount, our invoices in their period they left out, our payments (with the bank or
person evidence) they have not applied or applied at another amount, credit notes we hold they have
not applied, credits they show that we cannot place ("record it before paying again"), and the
balance. Each difference carries the evidence on both sides and its effect on their balance against
ours, so the balance gap is explained line by line; what no line explains (usually an opening balance
from before Helm's records) is said to be unexplained. Rounding up to a parameter (₹1 by default)
agrees.

**The mandate** `reconcile_supplier_statements` (goal, "Run payables well", order 13): statements
dated in the last 45 days (a parameter); one `draft_reconciliation` action per statement that
differs, proposed once (drafted, rejected or undone ones are not proposed again; the next month's
statement is a new one). The instrument says "unknown" when there are no statements, rather than
guessing. No value is claimed. The action (`helm_actions.kind` gains `draft_reconciliation`, migration
`h1a3_helm_reconcile`) is an outbound draft like `draft_dispute`: the Guard's facts check asks a
person when the statement was not checked; `hands.draft_reconciliation` renders
`templates/emails/helm.render_reconciliation` (each difference in the supplier's terms, then both
balances) and sends only in Act mode to a known contact, never from the sample book. The cockpit
lists the differences under "Why", and an executed draft shows the email with "Copy the email".

**Sample company.** Sahyadri Packaging's statement for last month plants three differences (an
invoice Meridian never received, the month's invoice with freight added, Meridian's latest payment
not yet applied); its plan proposes exactly those, the gap explained to the paisa
(`tests/engines/test_helm_reconcile_live.py`).

## 18. The guided tour (owner addition C; `components/helm/HelmTour.tsx`)

On the sample company only: "Take the guided tour" (Start, and the sample's cockpit) walks six steps:
the instruments, the flight path, the plan and its Guard, a certificate, the Time machine, then "Your
turn" (set Helm up on your own workspace). The step is in the address (`?tour=2`), so a demo can be
reloaded or shared mid-tour and the browser's back button walks back. Each step outlines the part of
the page it explains (`data-tour="…"`) and scrolls it into view; the panel is a non-modal dialog
whose heading takes focus; Escape or "End the tour" closes it. Spec 82 walks it and approves the
sample's reconciliation.

