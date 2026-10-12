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
