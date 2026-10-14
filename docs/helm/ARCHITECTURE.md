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
not count towards `limit.workspaces`, and deleting it removes the sample book.

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
Reads use one REPEATABLE READ transaction (`helm/snapshot.py`), so a plan never sees half a rebuild.

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

All five are in `TENANT_ACTIVITY_JOB_TYPES` and `BILLING_GATED_JOB_TYPES`, gated on
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

## 13. Performance budget (measured on the sandbox; filled in when measured)

| Budget | Target | Measured |
|---|---|---|
| Plan for 1,000 open payables over 30 days | ≤ 10 s | _pending_ |
| 1,000 futures | ≤ 3 s | _pending_ |
| Incremental ledger update | ≤ 1 s per document | _pending_ |
| Event loop | no database work on the loop (threadpool rule) | _pending_ |
