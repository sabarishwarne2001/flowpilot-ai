# Hand-over between the four pre-launch campaign sessions

The final pre-launch campaign is split into four Claude Code sessions that run one after another,
each owning one slice of the product: **session 1** tenancy, roles, plans, seats, billing and usage
limits (the money and access model); **session 2** documents, AI and every engine, the whole
document-to-decision pipeline and every background job; **session 3** the organization, identity
and governance consoles, settings truthfulness and the security red team; **session 4** the
product shell and experience, performance, operations, end-to-end customer journeys and launch
certification. A session that finds a defect in another session's slice fixes it when that takes
under about 30 minutes; otherwise it writes it here, under the session that owns it, with enough
detail to reproduce. Each session appends a section "Session N → later sessions" when it finishes:
what it changed that later sessions must build on (limits, capability keys, metered keys,
migrations, renamed routes, shared components), defects it saw in their slices, and what in its
own slice is unfinished, with why.

## Owner request: the one-week trial (must be built in a later session)

The owner decided on 2026-10-11 (NEEDS-OWNER.md **N-052**, superseding the trial part of N-051) that
**Free is a one-week trial; when it ends the organization is asked to subscribe to a paid plan**, as
Claude does. Session 1 recorded it but did not build it. **Session 2 builds it** (ending a trial
stops uploads, the assistant, automations and engine work, which session 2 owns); **session 3**
checks the consoles, emails and settings it adds; **session 4** proves the whole journey (sign up →
trial → reminder → trial ends → subscribe → everything back) in the browser and certifies it. The
spec, with what already exists to build on:
1. **Clock.** One trial per owner account, starting when the person creates their first
   organization (`organization_service._start_on_free_plan` is the moment; the Free pool,
   `quota_service.usage_pool`, already groups an owner's organizations). Store when it ends
   (for example `users.trial_ends_at`, or on the pool's anchor organization) in a migration after
   `s1b1_calendar_feed_org_member`; length from a setting `FREE_TRIAL_DAYS = 7`.
2. **Gate, server side, every path.** After the end, without a live paid subscription: refuse new
   work with a 402 `TRIAL_ENDED` (`remedy: SUBSCRIBE`, `trial_ended_at`) in `plan_admission`
   (documents, re-processing, assistant, workspaces), in `app/api/billing_write_gate.py` (the
   read-only write gate, so every write route refuses the same way), and in
   `app/workers/tenant_gate.unpaid_reason` (so queued jobs skip, like F-255). Reading, search,
   export, member management, deletion, checkout and the billing portal stay open.
3. **Subscribing lifts it.** A live paid subscription (`subscription_service.live_subscription_for_organization`)
   overrides the trial at once; the checkout already works for a Free organization.
4. **Screens.** A trial banner with the days left (from day 5); after the end, owners get a
   full-page "Your trial has ended; choose a plan" with `PlanSelector`, others "Ask <owner> to
   subscribe" (`useAskPlanOwners`). The plan-allowance read (`GET /workspaces/{id}/usage/plan-allowance`)
   should carry `trial_ends_at` so every screen reads one source.
5. **Emails.** Two days before the end and on the day it ends (session 3 owns the templates).
6. **Tests.** Proven by failing tests first: the clock per owner account (a second organization does
   not restart it), each refused path after the end, the worker skip, subscribing lifting it, and
   a browser journey. The e2e Free tenant P must be seeded inside its trial (or with a fixed clock)
   so the existing Free-plan specs keep testing the Free limits.
7. **Docs.** Regenerate `docs/hardening/TENANCY-AND-PLANS.md` once the trial is in the code
   (`backend/scripts/generate_tenancy_doc.py`), and mark N-052 built.

## For session 2 (documents, AI, engines, background jobs)

_Session 2 is finished (PR #20); nothing was left here._

## For session 3 (organization, identity and governance consoles, security)

Everything left for session 3, including what sessions 1 and 2 handed on below, is itemised in
`docs/hardening/CAMPAIGN-MAP.md` §3 (written 2026-10-12, before session 3 started).

## For session 4 (shell, experience, performance, operations, journeys, certification)

Everything left for session 4 is itemised in `docs/hardening/CAMPAIGN-MAP.md` §4, and the owner's
launch steps collected so far in §5.

## Session 1 → later sessions

Session 1 (tenancy, roles, plans, seats, billing, usage limits) fixed F-236 to F-255 and took the
product decisions N-040 to N-051 (NEEDS-OWNER.md, each marked `DECIDED (campaign session 1)`).
The generated source of truth for who a customer is, what each role and plan allows and how every
request resolves its organization and plan is `docs/hardening/TENANCY-AND-PLANS.md`
(regenerate with `python backend/scripts/generate_tenancy_doc.py`; a test fails when it drifts).

**Build on these (all sessions):**
- **Plans.** Free is deliberately small and charged at acceptance: 25 `document.upload`, 50
  `ocr.page` (reserved at upload, including documents still waiting for OCR), 30
  `assistant.message` a month, 10 MB files, 10 pages a document, 250 MB stored, one workspace,
  two seats, no API keys, webhooks, automations, batch or Business engines. One Free allowance per
  owner account (`quota_service.usage_pool`). Paid allowances are **per seat** (multiplied by the
  live subscription's seats). Plan limits are `limit.*` rows (`app/core/plan_limits.py`).
- **One admission point.** Every way a document enters goes through
  `app/services/plan_admission.py` (`admit_document`, `assert_reprocess_admitted`,
  `assert_workspace_available`, `admit_assistant_message`). A new intake path must call it; a
  refusal is a machine-readable 402 (`SPEND_LIMIT_EXCEEDED` / `PLAN_LIMIT_EXCEEDED` with `reason`,
  `limit_key`, `remedy`, `resets_at`).
- **New capability `capability.automations`** (Developer and up). The worker's automation handler
  and marketplace installs check it; a new automation entry point must too.
- **Seats.** Every way a person joins checks `seat_capacity_service` under the per-organization
  seat lock (take it before any invitation row lock). Owner, Admin and Billing buy seats
  (`PUT /organizations/{id}/billing/seats`, price shown first).
- **Worker gate.** `app/workers/tenant_gate.py` skips tenant-activity jobs for archived or
  suspended organizations and, now, for organizations read-only for non-payment
  (`BILLING_GATED_JOB_TYPES`). A new tenant job type belongs in `TENANT_ACTIVITY_JOB_TYPES`.
- **"Ask someone" names people.** `AskPlanOwners` / `useAskPlanOwners` (frontend) read the plan
  owners from the member-readable billing summary; use them instead of "ask an administrator".
- **Migrations.** Head is `s1b1_calendar_feed_org_member` (after `s1a1_upload_session_part_sizes`).
- **Seeds.** `seed_quota_tiers.py` and `seed_price_book.py --version auto` must be re-run on deploy
  (new tiers, the automations capability, Gemini overage rates); existing paid subscribers keep the
  version they bought (`--carry-forward` moves only to equal-or-better).

**For session 2 (documents, AI, engines, jobs):**
- Any new job type that does work for one tenant: add it to `TENANT_ACTIVITY_JOB_TYPES` (and it is
  then billing-gated too unless it is an export).
- Re-processing and packet splits already go through plan admission; a new engine that runs OCR or
  stores bytes must reserve through `plan_admission` rather than meter after the fact.
- Free-plan e2e tenant P is on Free: browser tests there only exercise refusals, never spend its
  allowance.

**For session 3 (consoles, identity, security):**
- `NoAccess` ("Contact an organization owner…") and `AccessRestricted` (`askWho` role names) still
  name roles, not people; `useAskPlanOwners` can supply names where the viewer is a member.
- Review-assignment and obligation ownership still require an explicit workspace grant (org
  owners/admins without one cannot be assigned); deliberate, but worth a product look.
- Rate limits are per IP and per API key; there is no per-tenant request limit for browser
  sessions (cost is bounded by the quotas). Decide whether one is needed.

**For session 4 (journeys, operations, certification):**
- **Owner actions before launch:** enable plan switching in the Stripe customer portal (N-050);
  publish yearly plan prices (N-051); re-run the seeds on deploy.
- F-256 (open, P3): late usage into an hour that had no earlier usage is not disclosed as late, so
  `test_late_usage_is_disclosed_rather_than_hidden` fails when it runs at minute 59. Reproduce by
  pinning that test's `now` to `datetime.now(timezone.utc).replace(minute=59, second=30) - timedelta(hours=1)`.
  The fix is in how rollup windows are sealed (a late event into an hour with no window should be
  disclosed too).
- Refunds are not modelled: a Stripe `charge.refunded` is recorded as "credit note not modelled",
  so invoices stay "paid" and RevOps revenue does not subtract refunds (Dodo, as merchant of
  record, owns refunds). Unverified how far this skews the revenue dashboard; needs a decision.
- Browser-suite isolation: a test that signs a shared persona out everywhere breaks other tests
  running as that persona in parallel. Session 1 changed `30-auth`'s "session ended elsewhere" to end
  only its own sign-in (`DELETE /auth/sessions/{sid}`); keep new tests to that pattern.
- The admin RevOps / COGS endpoints are proven to refuse tenants; their operator behaviour was not
  exercised end to end in session 1.

**Unfinished in session 1, and why:**
- Plan changes for paying customers go through the gateway's own portal (N-050); an in-app
  "change plan" that modifies the live subscription was not built, because it could not be
  verified without live gateway credentials.
- A second gateway subscription that somehow arrives anyway (e.g. completed from an old checkout
  tab opened before the first) still fails to record (one live subscription per account) and
  dead-letters; it is not refunded automatically. Rare after F-253; an alert and refund playbook
  would close it.

## Session 2 → later sessions

Session 2 (documents, AI, every engine, every background job) built the owner's one-week trial
(N-052, details N-053), fixed F-257 to F-271 (FINDINGS.md, session 2 section) and took decisions
N-053 to N-056 (NEEDS-OWNER.md, each `DECIDED (campaign session 2)`).

**Build on these (all sessions):**
- **The trial.** Free is a seven-day trial per owner account (`organizations.trial_ends_at`,
  migration `s2a1_free_trial_clock`, now the one head; `FREE_TRIAL_DAYS`). After it a Free
  organization is read-only: 402 `TRIAL_ENDED` (remedy `SUBSCRIBE`) from
  `app/services/trial_service.py`, enforced in `billing_write_gate`, `plan_admission` (every
  intake path, public links included) and the worker gate (`tenant_gate.unpaid_reason`).
  A new write path or intake path inherits it by going through those, as before. New hourly job
  `trial.reminders` (two emails). e2e tenants S (two days left) and T (ended).
- **Models and money (N-054).** The platform's own keys serve only GPT-OSS 20B and 120B; any other
  model runs on the organization's own key, and a platform call naming one is answered by the
  default (`app/core/ai_models.py`: `platform_safe`, `requires_own_key`). Costs are the suppliers'
  list prices. **Re-run `seed_price_book.py --version auto` and `seed_quota_tiers.py` on deploy**
  (supplier costs; Business 40,000 OCR pages and 100M input tokens a seat; Enterprise 300M/75M).
- **API keys (N-056, F-271).** Default deny: an API key may call only a route in
  `ROUTE_SCOPE_MAP` (`app/core/scopes.py`) or one declaring `RequireScope(...)`, checked centrally
  in `get_current_user`. **A new route meant for integrations must be added to the map**, or keys
  get 403.
- **Reprocess** (single and bulk) goes through `document_intake_service.queue_reprocess`: it locks
  the document, refuses while a read or enrichment job is live, resets the pipeline stage and
  queues a new run whose `run_id` gives enrichment its own metering scope
  (`llm_metering.enrichment_scope`). Anything else that re-runs a document should call it.
- **Worker.** A job's lease now starts when it starts (`claim.start_job`), a running job's lease is
  renewed by a heartbeat (`worker._LeaseKeeper`), settling is fenced to the worker holding the job,
  and a stopping worker hands back unstarted jobs. A new loop that runs jobs must use the same.
  Platform staff see the queue and the dead-letter queue at **/admin/jobs** and can retry a dead job.
- **Matching (N-055).** One case per invoice; later copies are listed on it.
- **Running the backend tests locally:** point the app at the test database too
  (`POSTGRES_DB=flowpilot_test pytest …`); otherwise code that opens its own `SessionLocal` (the
  worker gate) reads the development database and the trial worker tests fail.

**For session 3 (consoles, identity, security):**
- F-271 was found from the document side: scopes had been checked only where a route opted in.
  Worth the same look at the other non-human principals (partner portal tokens, SCIM tokens, the
  public gateway's own key check) to confirm each is limited to its routes.
- `GET /organizations/{id}/members` and invitations are now refused to API keys (no scope covers
  invitations; `members:read` covers the list). If an enterprise asks for user provisioning by
  key, that is SCIM's job, not a new scope.

**For session 4 (journeys, operations, performance, certification):**
- **Real-model spot-checks** (this sandbox has no Groq/Gemini key and cannot reach the Hugging Face
  hub, so the model stand-in and a local embedding model were used): with a real key, check
  prompt-injection resistance on the corpus's injection documents, line-item extraction on the
  invoices, and the GST invoice; with real PaddleOCR weights, the photographed and handwritten
  corpus pages; confirm Groq's and Google's current prices against `seed_price_book.py`.
- New browser spec `35-platform-jobs.spec.ts` (the e2e seed puts one dead job back on every run).
- Dormant handlers, not defects: `batch.expand_archive` (zip intake is not offered in the
  product) and `work_items.bulk` (the bulk API runs inline, up to 500 documents). Time a 500-document
  bulk delete behind the production proxy; if it nears the gateway timeout, move bulk delete onto
  the existing `work_items.bulk` job.
- The production build warns that the main chunk is over 1,000 kB (pre-existing); worth a
  code-split pass for first-load time.
- **Late fixes from the ledger push (F-272 to F-274).** Using every item action for real found
  three more: replaying a finished answer ended in a traceback (F-272); a document with line
  items could not be reviewed at all (F-273: lists are never part of a review decision now);
  a goods receipt naming one order was attached to another order's invoice (F-274). The review
  and matching specs (13, 24, 34) pass on the production bundle.
- **Ledger, session 2's slice:** 362 rows `deep`, 9 `blocked` (dormant handlers and the jobs that
  need gateway, warehouse, ERP or supplier credentials), 83 `smoke`, 18 `untested`. What is left:
  the pages and navigation items are proven to render live with the CSP but their journeys are
  yours; the ops crons (backups, PITR drills, watchdog, DR heartbeat) are yours; and these item
  actions still have only their refusals proven: assertions (rule detail, simulate, resolve,
  delete a clause check), case files (detail, remove a document, close, revoke a request),
  corroboration (refresh, review, page image, delete), extraction memory (edit a template or
  rule), packet splits (start, edit boundaries, reject), process proposals (detail, approve,
  reject, undo), redactions (page image, edit a region), an ERP target credential and a failed
  posting's retry, the public workflow trigger, analytics schedules. The pattern to copy is
  `backend/tests/engines/test_item_actions_*.py`; `docs/hardening/tools/coverage_from_engines.py`
  turns a test run's route log into ledger rows (engine tests: `ENGINE_ROUTE_LOG`; API tests:
  `API_ROUTE_LOG`).

## Campaign review before session 3 (2026-10-12)

Not a session: a review of the whole repository between sessions 2 and 3, so that sessions 3 and 4
cover what no prompt named. Its output is `docs/hardening/CAMPAIGN-MAP.md` (every open item with
its owner session, why, and how to close it) and `docs/hardening/tools/campaign_slices.py` (which
session owns each coverage row that is not `deep`; `--check <3|4|all>` is the finish line).

What later sessions must know:
- **CI runs on every pull request, and has been red on most runs since 2026-10-06** (F-275). Sessions
  1 and 2 read the commit status API, found nothing and wrote "no CI runs on this repo's PRs";
  Actions reports through check runs (`gh pr checks <number>`). A red pytest or browser job now
  names each failing test in an annotation and in the job summary
  (`.github/scripts/annotate_test_failures.py`). A session's pull request is ready only when CI is
  green on its last commit.
- `docs/hardening/tools/extract_endpoints.py` now mounts `document_evidence.py` and
  `document_fields.py` under `/work-items` and resolves an imported path constant (the review
  WebSocket's `v.WS_SUFFIX`), so its output matches the code: 621 operations, of which 4 have no
  ledger row yet.
- Re-running a session prompt after a cut-off: continue on the open pull request of that session
  (title starts "Campaign session 3" or "Campaign session 4"), not on a new branch from `main`;
  the unticked boxes in CAMPAIGN-MAP.md are where to resume.

## Session 3 → later sessions

Session 3 (organization, identity and governance consoles, settings truthfulness, security,
notifications and email) fixed F-276 to F-325 (FINDINGS.md, session 3 section; F-324 is recorded
as **unverified**, below), took decisions N-057 to N-068 (NEEDS-OWNER.md, each
`DECIDED (campaign session 3)`), wrote `SECURITY-REVIEW.md` and `SETTINGS-LEDGER.md`, and closed
every session 3 ledger row (`campaign_slices --check 3` exits 0; `branding.domain` is blocked with
its reason).

**Build on these (all sessions):**
- **Sign-in policy everywhere (N-057, F-276 to F-278).** "Require single sign-on", the IP
  allowlist and "require two-factor" (N-060, every plan) are enforced at sign-in, on refresh and on
  the live review WebSocket. The IP allowlist pins SSO sessions only; for password sessions the
  console shows it as unavailable. A new way to authenticate must go through the same policy check.
- **Identity after a downgrade (N-059, F-279 to F-282).** SCIM tokens and SSO provisioning stop
  when an organization leaves Enterprise, is read-only or archived. `idp_session_sync` is gone (N-058).
- **Notifications (N-062, F-287 to F-290).** Every notice appears in the app; billing and security
  notices are always emailed, others per the person's preferences (`/me/notification-preferences`).
  A new notice goes through `organization_notification_service.emit` with a category; plan-limit
  alerts at 80% and 100% are the `usage.limit_alerts` job.
- **Rate limits (N-061, F-286).** Signed-in traffic is limited per person, not per network address.
- **Egress (F-291).** Every outbound connection goes through `app/core/egress.py`; a structural
  test fails on a new raw client. An organization's lockdown and rules are enforced (live test).
- **Audit (F-294, F-295, F-304, N-067).** Identity and webhook changes are audited. The nightly
  ARCH-07 sweep deletes audit entries per organization by its own retention period (blank keeps
  them). **On deploy:** the four host crons installed (session 4, F-355: the sweep deletes through
  the application's database unless `AUDIT_SWEEPER_DATABASE_URL` names a separate login; before,
  it skipped or was refused by the audit trigger every night)
  (`backend/deploy/cron.d/flowpilot-sweepers`: arch07, arch09, identity, invitations).
- **Display layer (N-064, F-298).** One module per side: `frontend/src/utils/displayTime.ts` and
  `backend/app/core/display_format.py`. The person's own zone and language win; the open
  workspace's clock and date format stand in. `npm run check:self` ("one display layer") fails on
  a stray `Intl.DateTimeFormat`. A change to the workspace clock or date format re-renders the
  workspace's pages; pages keep their place through the address (the settings page writes
  `?section=`, F-322).
- **Workspace regional settings (N-068, F-320, F-321).** `app/schemas/regional.py` checks time zone,
  currency, date format and language on workspace and organization creation and update. Rows saved
  before F-320 are not migrated: **check once on deploy** that no workspace holds a value outside
  those checks (one SQL query; the page accepts any value the browser can use).
- **Brand colours (N-065, F-300)** reach the sign-in pages and the app on a verified domain
  (`frontend/src/utils/brandTheme.ts`); **data residency (N-066, F-301)** states what the region
  pins, kept equal to the code by a structural test.
- **Rules from events.** An event meant for workflow rules must be written with
  `outbox_service.emit_trigger` (event and `automation.execute` job together). `emit_internal`
  alone reaches no rule; that is how the public API's trigger did nothing (F-323).
- **People, not IDs.** Access screens and the erasure form name people (F-325). Anything new that
  asks an administrator to choose a person should offer a picker from the member list
  (`include_inactive=true` for former members), never a raw ID.
- **Partners.** A partner is created with its first owner (`owner_user_id`, F-310); a
  revenue-share agreement is ended with `POST /partners/{id}/agreements/{agreement_id}/end` (F-309);
  a suspended partner's portal is read-only (F-319).

**For session 4:**
- **F-324 (unverified, verify first).** With multi-agent verification on, a document's "Document
  processed" rules may be skipped for good when the engine job runs while the verify job is
  mid-flight: the verify job commits a PENDING record before calling the agents, the engine skips
  rules while one is pending, and the agents' release event is written with `emit_internal` and no
  engine job (the human-review path re-queues it; this path does not). Not reproduced in tests; a
  fix must not run rules twice (runs are de-duplicated per rule and event, not per document).
  `test_verified_documents_run_their_rules_live.py` pins "exactly once" for the ordering that works.
- **`/no-access`** is registered and now tested by address, but nothing routes there; the guards
  handle suspended and archived tenants inline (`16-lifecycle`). Wire it to `TENANT_SUSPENDED` or
  remove it.
- **Browser-test re-runs on one database.** On a reused local e2e database, `22` and `23` fail on
  leftover "rescan" documents from earlier runs; CI (fresh database) passes. Make them re-runnable.
- **Unproven settings** (SETTINGS-LEDGER "Still unproven"): the assistant's temperature, dispatch
  tagging off, extraction memory off, SLA at-risk and alerts, agent policy fields, procurement
  tolerances, conversation retention.
- **Legal name** reaches the payment provider only when the customer is first created; later edits
  do not (SETTINGS-LEDGER). Decide: push updates, or say so on the page.
- **Dead or partial:** the `EmailSettings` table is read by nothing; platform staff have no screen
  for a partner's status or agreements (API only); the organization identifier cannot be changed
  anywhere (the page says so, F-308; offering a rename is a product choice with redirects to build).
- **Consistency nits:** OIDC configuration errors answer 400 where SAML answers 422; Unit economics
  says "0 events" where suppression would read the same; RevOps does not list promo redemptions;
  the Developer "Issue key" dialog has no `role="dialog"`; BYOK "Retire" has no confirmation.
- **`branding.domain` (blocked):** the claim/verify screens need a real DNS zone; worth one check on
  a staging domain before launch. Every route they call is live-tested.
- **Docs:** CONFIGURATION.md drifts from the settings when regenerated; the Tailwind 4 move and the
  main chunk's size are still in the map.
- **Running tests locally:** never run two pytest sessions at once (the conftest truncates the
  test database at start); after a backend change restart the e2e API (`kill` the pid in
  `/tmp/flowpilot-e2e-logs/pids/api.pid`, then `E2E_LLM=1 frontend/e2e/scripts/start-stack.sh`)
  before running browser specs.

## Session 4 → later sessions

Session 4 (experience, performance, operations, launch certification; then, at the owner's
request, TruthMesh, Batch Operations and Document Packs in depth) fixed F-324 to F-409 (FINDINGS.md,
session 4 sections), took decisions N-069 to N-077 (NEEDS-OWNER.md, each
`DECIDED (campaign session 4)`), and closed every ledger row (`campaign_slices --check all` exits 0).
**What is left is listed, with instructions, in `docs/hardening/NEXT-SESSION-PROMPT.md`** — paste it
into a new Claude Code session to continue.

**Build on these (all sessions):**
- **Browser tests are the proof for screens.** The suite (`frontend/e2e/tests`, 58 files, 811 tests) runs
  in CI against the production bundle with the worker on; it is a required check. New since this
  session: every page at four widths (74), every page in light and dark through axe-core (72), the
  first run (73), TruthMesh (75), batch operations (76) and document packs (77) in depth, each on a
  tenant of its own so they never race the shared seeded tenants.
- **No database work on the event loop.** An `async def` route must hand database work to the
  threadpool and reload expired objects there (F-218, F-363); a test fails on a query run on the loop.
- **Mesh writes are serialized per workspace (F-382).** Anything that writes TruthMesh rows takes
  `truthmesh.service.lock_workspace` first.
- **Uploaded archives are hostile (F-390, F-407).** Package verification caps what it reads and
  turns every unreadable entry into a verdict; any new upload parser needs the same.
- **Encryption-key rotation covers every secret column (F-370).** A new `*_encrypted` or
  `*_ciphertext` column fails a test until the rotation sweep covers it.
- **Emails:** every transactional email uses `BASE_EMAIL_HTML_LAYOUT` (F-380); a new one must pass
  `test_emails_render_in_mail_clients.py`.
- **Say what a feature does.** Document packs today apply their field list only (N-075, F-408);
  PRODUCT-NEXT item 10 wires the rest.

**Known, not done (details and order in NEXT-SESSION-PROMPT.md):** the audit's unverified TruthMesh,
batch and pack items (FINDINGS.md, "Session 4 (continued) — … unverified"); N-076 and N-077 to build;
32 features whose browser coverage is partial (the coverage-gap table in the prompt file); Tailwind 4
and least-privilege database roles (N-074).

## Flagship session → website and payments session

The flagship session built **Helm** (docs/helm/ARCHITECTURE.md is the design, kept current;
docs/helm/MANDATES.md lists the mandates in plain words; docs/product/FLAGSHIP.md is the product
story for the website). Its decisions are N-082 to N-097 in NEEDS-OWNER.md (`DECIDED (owner and
planning chat, 2026-10-15)` or `DECIDED (flagship session)`); its defects are in FINDINGS.md under
"Flagship session". Migrations `h1a1_helm`, `h1a2_helm_bank`, `h1a3_helm_reconcile` (one head).

**For the website and payments session:**
- **Plans and copy.** The plan cards already list Helm (`frontend/src/constants/planEntitlements.ts`:
  `capability.helm`, `limit.helm_mandates`, `limit.helm_backtest_months` and the Enterprise
  capabilities). Prices did not change (N-082). Use FLAGSHIP.md for the website's Helm page and its
  "what we never claim" list; screenshots come from the sample company (Meridian Wholesale), which
  any account can build from Helm → Start and delete afterwards.
- **The rename to Servabo.** No new "FlowPilot" strings were added; Helm's own strings say "Helm".
  The sample company's documents name only invented companies.
- **Payments stay out of Helm.** Helm never moves money (N-088): the payment run is a list a person
  pays from their bank, and bank statement imports bring the evidence back. Nothing in the payment
  gateway work should change that.
- **Mail.** Helm sends through the organization's transactional sender ladder: the Monday briefing,
  receivables reminders, compliance-document requests, disputes and supplier reconciliations (the
  last two only in Act mode with a known contact). A new branded email layout must keep
  `test_emails_render_in_mail_clients.py` green; Helm's notices now carry an "Open Helm" button
  (`organization_notification_service.APP_ACTIONS`).
- **Performance.** ARCHITECTURE §13 has the budget measured at 1,000 and 10,000 documents
  (`tests/performance/test_helm_budgets.py`; `HELM_PERF_DOCUMENTS=10000` reruns the large one).

**Build on these:**
- Every Helm decision is deterministic code in `backend/app/services/helm/`; the only model call
  drafts a mandate from a sentence (`drafting.py`, metered under `llm:helm:`).
- A bank statement is evidence only when a bank issued it: `ledger.statement_kind` keeps a
  supplier's statement of account out of payments and cash.
- Matching a payment to an invoice (bank imports and statement documents alike) uses one rule set,
  `bank_match.mentions_document` and `mentions_payee`: a whole invoice number, or every distinctive
  word of the payee; never one shared word.

## Owner launch checklist

Only you can do these. They are in the order to do them; each says where the detailed steps are.
Tick them in a copy of this file or in your notes. Nothing here needs code changes.

**A. Accounts and keys (a day or two before the first deploy)**
1. **Rotate every key ever pasted into a chat or committed** (session 3's history scan lists them in
   SECURITY-REVIEW.md). Create new ones; the old ones must stop working.
2. **Stripe, test mode first** (N-026, N-050, N-072, F-125):
   - one per-seat monthly price for Developer ($49), Business ($299) and Enterprise ($799); put their
     ids in `GATEWAY_PRICE_ID_DEVELOPER/BUSINESS/ENTERPRISE` in `.env.production` (read by
     `seed_quota_tiers.py`);
   - optional yearly prices: Developer $490, Business $2,990 per seat (N-051);
   - Developers → Webhooks: the endpoint, the events and the signing secret exactly as RUNBOOK §9
     step 9 lists them (`/api/v1/billing/webhooks/stripe`; the secret goes in `STRIPE_WEBHOOK_SECRETS`);
   - Settings → Billing → Customer portal: allow customers to **switch plans** between Developer,
     Business and Enterprise with **proration**, to **change quantities**, to update payment methods
     and to cancel at the period's end (the plan picker opens the portal on the chosen plan and
     seats, F-344);
   - Billing → Revenue recovery → Retries: Smart Retries, and "if all retries fail" → **mark the
     subscription as unpaid** (not cancel) (N-072);
   - run one test checkout, one plan change in the portal and one cancellation; then switch to live
     keys and repeat with a real card for one seat.
3. **Postmark** for email (N-027): its SMTP server and token in the `PLATFORM_SMTP_*` settings of
   `.env.production` (the app refuses to start without a mail server), the SPF and DKIM records it
   gives you added at your DNS provider, and a sender signature for `no-reply@` your domain.
4. **Operator alerts** (N-070): `OPERATOR_ALERT_EMAILS` = your address (the app refuses to start
   without it in production).
5. **Groq**: a key in `GROQ_API_KEY`; on Groq's and Google's price pages, check the prices in
   `backend/scripts/seed_price_book.py` (N-054).

**B. Server (RUNBOOK §9)**
6. A server of **4 vCPU, 16 GB RAM, 160 GB SSD** (COSTS.md), Docker installed.
7. **DNS:** an A record for `app.YOUR-DOMAIN` pointing at the server **before** Caddy's first start
   (Caddy gets the certificate on its first request); a mailbox for `admin@` (N-028).
8. Follow RUNBOOK §9.1 to §9.3: clone, `.env.production` from the template, pin images by digest
   (N-017), `docker compose up -d`, then `seed_quota_tiers.py` and `seed_price_book.py --version auto`
   (also on every later deploy, §9.3).
9. **Backups (§9.4):** create `/etc/flowpilot/backup.key` and copy it into your password manager
   **now**; set `FLOWPILOT_BACKUP_MIRROR_CMD` to copy each backup to another provider; run one backup
   and one restore drill by hand and note the restore time.
10. **Scheduled jobs and monitoring (§9.6):** install `/etc/flowpilot/sweepers.env` and the two cron
    files, switch off the three point-in-time lines with the command given, create free
    Healthchecks.io checks (uptime with a 1-minute period, the backup, the restore drill, the
    retention sweep) and set `ALERT_WEBHOOK` for the sweeper watchdog. Check them once by hand.
11. **The CPU-only image** (F-045): build it on a machine that can reach download.pytorch.org and
    push it to your registry; use its digest.

**C. Before the first customer**
12. **Legal:** have counsel replace the placeholder terms and privacy pages (`/terms`, `/privacy`;
    N-029, N-073) and add a support or sales email to the pricing page footer.
13. **Real-model spot checks** (sessions 2 and 3): a prompt-injection document, an invoice with many
    line items, an Indian GST invoice, a photographed page and a handwritten page; check the fields
    and the review queue. Real OCR (PaddleOCR) on scans was never run in the test sandbox.
14. **Enterprise identity on a staging domain:** claim and verify a real domain with a TXT record
    and sign in once through a real Okta or Entra ID test tenant (`branding.domain` and the live
    IdP were stood in by tests).
15. **One SQL check** for workspace regional settings saved before F-320 (HANDOFF, session 3).
16. Merge the last pull request; deploy; sign up as a new customer yourself and go through the
    first-run checklist end to end.
17. **After any encryption-key rotation** (`EMAIL_ENCRYPTION_KEYS`): run the re-encryption sweep and
    its `--verify` (RUNBOOK) before removing the old key; F-370 made it cover every secret.
18. **Healthcare customers:** tell them the HIPAA redaction profile removes names, dates of birth,
    SSNs, card numbers, emails and phone numbers; the other Safe Harbor identifiers need a manual
    pass (N-077).
