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
  (`POSTGRES_DB=servabo_test pytest …`); otherwise code that opens its own `SessionLocal` (the
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
  (`backend/deploy/cron.d/servabo-sweepers`: arch07, arch09, identity, invitations).
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
  `/tmp/servabo-e2e-logs/pids/api.pid`, then `E2E_LLM=1 frontend/e2e/scripts/start-stack.sh`)
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
story for the website). Its decisions are N-082 to N-102 in NEEDS-OWNER.md (`DECIDED (owner and
planning chat, 2026-10-15)` or `DECIDED (flagship session)`); its defects are in FINDINGS.md under
"Flagship session". Migrations `h1a1_helm`, `h1a2_helm_bank`, `h1a3_helm_reconcile`, `h1a4_helm_api_vocabulary` (one head).

**For the website and payments session:**
- **Plans and copy.** The plan cards already list Helm (`frontend/src/constants/planEntitlements.ts`:
  `capability.helm`, `limit.helm_mandates`, `limit.helm_backtest_months` and the Enterprise
  capabilities). Prices did not change (N-082). Use FLAGSHIP.md for the website's Helm page and its
  "what we never claim" list; screenshots come from the sample company (Meridian Wholesale), which
  any account can build from Helm → Start and delete afterwards.
- **The rename to Servabo.** No new "Servabo" strings were added; Helm's own strings say "Helm".
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
  One item is over budget: the ledger update after one document change takes 2.9 s at 10,000
  documents in a workspace (budget 1 s; within it up to about 2,000). Nobody waits on it (it runs in
  the worker, debounced), but the next step is a truly incremental ledger: today
  `ledger_store.rebuild_workspace` re-projects the whole workspace; instead re-project only the
  promises of the changed document, its counterparty and the bank lines that can match it
  (`lines_by_amount` already narrows them), and keep the full rebuild as the nightly check that the
  two agree.

- **API keys and webhooks.** Enterprise keys reach Helm with `helm:read` / `helm:write`; approving and
  Helm's rules stay with people (N-102). A key now works only in the organization that issued it
  (F-435): if the developer portal shows keys across organizations, it must keep to that.

**Build on these:**
- Every Helm decision is deterministic code in `backend/app/services/helm/`; the only model call
  drafts a mandate from a sentence (`drafting.py`, metered under `llm:helm:`).
- A bank statement is evidence only when a bank issued it: `ledger.statement_kind` keeps a
  supplier's statement of account out of payments and cash.
- Matching a payment to an invoice (bank imports and statement documents alike) uses one rule set,
  `bank_match.mentions_document` and `mentions_payee`: a whole invoice number, or every distinctive
  word of the payee; never one shared word.

## Website and payments session → next sessions

This session (branch `claude/new-session-3qnrpb`, PR #25) renamed the product to **Servabo**, built
the public website in `website/` and joined it to the app, and made **Dodo Payments** the real
payment gateway. Its decisions are in NEEDS-OWNER.md as `DECIDED (website and payments session)`
(and the owner's own as `DECIDED (owner, planning chat, 2026-10-15)`); its defects are in
FINDINGS.md under "Website and payments session". Migrations `w1a1_servabo_database_names`,
`w1a2_billing_disputes`, `w1a3_scheduled_plan_change`, `w1a4_hold_notices` (one head).

**What changed (build on these):**
- **Name.** Everything people see says Servabo. History files (FINDINGS, NEEDS-OWNER, STATE,
  CHANGELOG entries before 2026-10-16) keep the old name; `backend/tests/core/test_the_old_name_is_gone.py`
  fails when the old name appears anywhere else outside its allowlist. The old-prefix settings, `fp_live_`/
  `fp_test_` API keys and the `--loop stripe` / `worker-stripe` names still work **for one release**
  with a start-up warning; remove those aliases in the next release (`app/core/legacy_names.py`,
  `api_key_secret.LEGACY_PREFIX`, `app/worker.py` `LOOP_ALIASES`, `app/db/session.py` `ROLE_ALIASES`).
- **Renamed protocol identifiers** (each breaks once for an existing integration; the table with
  old and new values is in CHANGELOG.md, the rename entry of 2026-10-16): the outgoing webhook
  signature and delivery headers (`X-Servabo-*`), User-Agent strings, the API version and spend-limit
  headers, the domain-verification TXT value and record names (`servabo-site-verification`,
  `_servabo.`, `_servabo-challenge.`), the live-review WebSocket subprotocol (`servabo.review.v1`), the
  refresh cookie (`servabo_refresh`: everyone signs in once more), API key prefixes (`svb_live_`,
  `svb_test_`), browser storage keys and the sovereign licence format (`servabo-licence/1`).
- **Website.** `website/` (Astro, static) with home, Helm, product, four solutions, pricing, security,
  about, contact and the legal pages; its prices come from `backend/scripts/export_public_pricing.py`
  (`website/src/data/pricing.json`, checked in CI), so the site and the app cannot disagree. Deploy:
  `docs/website/DEPLOY.md`. The app's `/pricing`, `/terms` and `/privacy` now redirect to it
  (`WEBSITE_URL` / `VITE_WEBSITE_URL`); emails carry its legal links. CI job `website`.
- **Dodo is the default gateway** (`BILLING_GATEWAY=DODO`). Everything Servabo calls and receives is
  in `docs/billing/DODO.md`. Checkout returns wait for the webhook; Dodo payments become invoices
  issued by Dodo with a download; refunds and disputes are recorded (a lost dispute makes the
  organization read-only until a later payment or the operator lifts it, RevOps → Disputes); plan
  changes happen on Servabo's Billing page with Dodo's own quoted amount (upgrades now, downgrades at
  the renewal, `billing.plan_changes`); a held renewal sends a reminder, a final notice and the
  read-only notice (`billing.hold_notices`); customer details are sent to the Dodo customer; yearly
  plans appear when their products are configured; the margin console counts Dodo's fees; every paid
  plan keeps at least 55% after fees at its ceilings (Enterprise pages per seat 95,000).
- **Usage on Dodo is hard limits.** Dodo cannot bill usage lines, so overage is refused, never billed
  (the plan cards and usage page say so). Usage billing is a product decision for later (NEEDS-OWNER).
- **Tests.** The stand-in Dodo is `backend/tests/fakes/fake_dodo.py` (API, checkout page, customer
  portal, Standard Webhooks signing). The browser suite bills through it by default
  (`E2E_GATEWAY=dodo`); CI also runs the customer journeys on Stripe. New journey 68 covers upgrade,
  invoice download, scheduled downgrade, refund and cancellation on Dodo.
  `tests/engines/test_dodo_concurrent_deliveries_live.py` delivers one subscription's whole life
  twice, at once, to a real server with four reconciling workers.
- **The database keeps historical names** (`stripe_inbound_events` holds Dodo events too, etc.;
  DODO.md §7). Do not rename them without a reason a customer would notice.

**Not yet verified against a real Dodo account** (everything was built from Dodo's documentation
and proven against the stand-in): the ten points in DODO.md §6 — the payment's `refunds`/`disputes`
fields, change-plan's body and `do_not_bill`, the preview amount equalling the charge, metadata after
a plan change, the return URL's parameters, the products list's paging, how live and test keys
differ, Dodo's own retry window, the webhook body's timestamp and the fee figures. The first session
with a Dodo test account should run `scripts/dodo_setup.py`, one checkout, one upgrade, one
downgrade, one refund and one card decline (Dodo's test cards), and compare with DODO.md.

**What is left:**
- Remove the one-release aliases (above) in the next release.
- Usage billing on Dodo (if the owner wants overage billed rather than refused): Dodo's usage-based
  billing or add-on quantities; a product decision first.
- Real Dodo test-account verification (above) and Dodo's account verification (KYC; the website must
  be online with pricing, terms, refund policy and contact).
- The ledger performance item from the flagship section is unchanged.

## Helm visual session → next sessions

_2026-10-17. Branch `claude/beautiful-dijkstra-yvlspo`, PR sabarishwarne2001/servabo#26._

**What changed.** Helm wears Night Bridge on every page (`docs/helm/VISUAL.md`), the Documents | Helm
switch glides and reveals Helm with a circle from the switch, Documents wears its own calm look (Lumen),
and the website's Helm page and every screenshot were redone. Decisions N-111 to N-116; findings F-462
to F-468.

**How to see it locally.** `.\start_dev.ps1` (or `./start_dev.sh`), sign in, open the sample company
from Helm's Start page (or press Helm in the header), then press Documents and Helm again to see the
switch. Try both themes (the sun/moon button) and a phone width.

**Keep in mind.**
- Read `docs/helm/VISUAL.md` before touching a Helm page. Use the classes and `components/helm/ui/`;
  every number on screen must come from the data (leave a badge out rather than invent it).
- Helm money goes through `formatHelmMoney` / `formatHelmMoneyCompact` (`components/helm/money.ts`);
  never `formatMoneyMicros` directly in Helm.
- The switch starts its own view transition (N-114). If the routes are ever moved to a data-router
  route tree, React Router's `viewTransition` option would work and the switch could use it instead.
- The entrance animations are short so axe never sees text mid-fade; keep them under a second.
- The website's CSP forbids inline styles and `data:` images: no `style=` in `.astro` pages, and the
  Helm page has no grain layer. Its switch demo is `public/helm-switch-demo.js` plus CSS in
  `website/src/styles/global.css`.
- Bundle: Helm's lazy chunks grew from 58.2 kB to the figure in STATE.md (gzipped), inside the 35 kB
  allowance; no new runtime dependency.

**Seen, not fixed (pre-existing).** Spec 34 leaves a rescanned copy of Tenant C's INV-E2E-1001 in
the workspace, so spec 22's "a filtered list pages over the matches only" finds 3 rows instead of 2
when it runs after 34 on the same database (a partial re-run). In a full run 22 comes first and
passes; a fresh database also passes. Spec 34 should delete its copy at the end.

**Not done / for later.** The Plan page's what-if is still the cockpit's panel (not a drawer of its
own); the template gallery's sheen is the shared panel hover. Findings F-465 to F-468 are fixed but proven
by screenshots only.

## Owner launch checklist

Only you can do these. Do them in this order; each step says what you need, where to click and how
you know it worked. Nothing here needs code changes. Rewritten in the website and payments session
(2026-10-17) for Servabo, the website and Dodo Payments. Tick them in a copy of this file.

**Step 1 — Rotate every key that was ever pasted into a chat or committed.**
SECURITY-REVIEW.md lists them. In each provider's dashboard create a new key and delete the old
one. *Done when:* every old key is deleted (the provider shows it as revoked).

**Step 2 — Buy the domain and add it to Cloudflare.**
Buy `servabo.app` (or your choice) at Cloudflare Registrar, or move an existing domain's DNS to
Cloudflare (free plan). You will create two addresses: `servabo.app` for the website and
`app.servabo.app` for the app. *Done when:* the domain shows "Active" in Cloudflare.

**Step 3 — Put the website online (about 15 minutes).**
Follow `docs/website/DEPLOY.md`, Option A (Cloudflare Pages). Set `PUBLIC_CONTACT_EMAIL`; leave the
phone number empty unless you want it public (never commit it). *Done when:* `https://servabo.app`
opens and its Pricing, Terms, Privacy and Refunds pages load. Have a lawyer read the terms, privacy
and refund pages before the first paying customer (N-029, N-073).

**Step 4 — Apply to Dodo Payments.**
Sign up at dodopayments.com. Verification asks for your ID and a selfie, your bank account (in your
own name) and your website: it must be online first (step 3). It takes 1–3 working days. Meanwhile
everything below works in **Test Mode**. *Done when:* Dodo's dashboard shows your business verified.

**Step 5 — Email, AI and alerts accounts.**
- **Postmark** (N-027): create a server, add the SPF and DKIM records it shows you in Cloudflare
  DNS, and a sender signature for `no-reply@servabo.app`. Keep its SMTP host and token for step 7.
- **Groq**: create an API key at console.groq.com/keys. Check the model prices in
  `backend/scripts/seed_price_book.py` against Groq's price page (N-054).
- **Cloudflare R2** (decided: EU jurisdiction): create a bucket with "EU" jurisdiction and an API
  token for it, for customers' documents.
*Done when:* you have the Postmark token, the Groq key and the R2 bucket name, endpoint and keys in
your password manager.

**Step 6 — Rent the server (Hetzner, decided).**
4 vCPU, 16 GB RAM, 160 GB disk, Ubuntu 24.04 (COSTS.md has the current price). In Cloudflare DNS add
an **A record** `app` → the server's IP address, **DNS only** (grey cloud), **before** the first
start, so the app can get its HTTPS certificate. *Done when:* `ping app.servabo.app` answers from
your server's address.

**Step 7 — Install Servabo on the server.**
Follow RUNBOOK §9.2 steps 1 to 7 exactly: the user and folders, the code, `.env.production` from the
template (secrets generated with the commands shown; Postmark, Groq, R2 from step 5;
`OPERATOR_ALERT_EMAILS` = your own address; `FRONTEND_URL=https://app.servabo.app`,
`WEBSITE_URL=https://servabo.app`; Dodo's **test** API key and `DODO_LIVEMODE=false`), the build, the
migrations and the start. *Done when:* `https://app.servabo.app/api/v1/health/ready` says ready.

**Step 8 — Create the plans in Dodo (test mode) and publish them.**
RUNBOOK §9.2 step 8: `dodo_setup.py --create-test-products` creates the five plan products and two
add-ons and prints seven lines for `.env.production`; then `dodo_setup.py` must say OK for all seven;
then the two seed commands and the first administrator. *Done when:* signed in as a new user, the
Billing page shows Developer $49, Business $299 and Enterprise $799 per seat.

**Step 9 — Connect Dodo's webhooks and buy a plan with a test card.**
RUNBOOK §9.2 step 9: add the webhook endpoint in Dodo (Test Mode), put its secret in
`DODO_WEBHOOK_SECRET`, restart. Then, as a new customer, buy Developer with Dodo's test card.
*Done when:* back in Servabo the page says "Payment confirmed", Billing shows the plan and the
invoice "issued by Dodo Payments", and its "Download invoice" opens Dodo's PDF. Also try: upgrade to
Business (the amount shown is what is charged), a refund from Dodo's dashboard (the invoice says
"Refunded"), and cancelling in "Manage payment method" (Billing says the plan ends at the period's
end). Compare what you see with `docs/billing/DODO.md` §6 and note anything different.

**Step 10 — Backups, scheduled jobs and monitoring (same day as step 7).**
RUNBOOK §9.4 and §9.6: create `/etc/servabo/backup.key` and copy it into your password manager
**now**; set `SERVABO_BACKUP_MIRROR_CMD` to copy backups to another provider; install the sweeper
cron files; create the free Healthchecks.io checks (uptime, backup, restore drill, retention sweep)
and set `ALERT_WEBHOOK`. Run one backup and one restore drill by hand. *Done when:* the drill
prints its restore time and every Healthchecks.io check is green.

**Step 11 — Go live with Dodo.**
Only after step 4 says verified and step 9 worked. In Dodo, switch off Test Mode, create the same
seven products in live mode (same prices: plan products per seat, recurring, tax not included), a
live webhook endpoint (same URL) and a live API key. In `.env.production`: the live key, the live
webhook secret, the seven live product ids, `DODO_LIVEMODE=true`; restart, and run the seed commands
again (step 8). Buy one seat of Developer with your own card, then refund it from Dodo's dashboard.
*Done when:* your own live payment shows in Billing and the refund shows as "Refunded".

**Step 12 — Before the first customer.**
- **Legal:** a lawyer reads the website's terms, privacy and refund pages (`servabo.app/terms`,
  `/privacy`, `/refunds`; N-029, N-073) and you publish their changes (edit `website/src/pages/`).
- **Real-model spot checks** (sessions 2 and 3): a prompt-injection document, an invoice with many
  line items, an Indian GST invoice, a photographed page and a handwritten page; check the fields
  and the review queue. Real OCR (PaddleOCR) on scans was never run in the test sandbox.
- **Enterprise identity on a staging domain:** claim and verify a real domain with a TXT record
  (`servabo-site-verification=…`) and sign in once through a real Okta or Entra ID test tenant
  (`branding.domain` and the live IdP were stood in by tests).
- **One SQL check** for workspace regional settings saved before F-320 (HANDOFF, session 3).
- **The CPU-only image** (F-045): build it on a machine that can reach download.pytorch.org, push it
  to your registry and use its digest; pin the other images by digest too (RUNBOOK §9.2 step 11).
- Merge the last pull request; deploy (RUNBOOK §9.3); sign up as a new customer yourself and go
  through the first-run checklist end to end.

**Later, whenever it applies**
- **After any encryption-key rotation** (`EMAIL_ENCRYPTION_KEYS`): run the re-encryption sweep and
  its `--verify` (RUNBOOK) before removing the old key; F-370 made it cover every secret.
- **Healthcare customers:** tell them the HIPAA redaction profile removes names, dates of birth,
  SSNs, card numbers, emails and phone numbers; the other Safe Harbor identifiers need a manual
  pass (N-077).
- **Next release:** remove the one-release aliases for the old name (HANDOFF, "Website and payments
  session → next sessions").
