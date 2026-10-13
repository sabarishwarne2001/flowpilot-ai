# Campaign map: everything left for sessions 3 and 4

_Written 2026-10-12, after sessions 1 and 2 were merged (PRs #19 and #20) and before session 3 started.
Sessions 3 and 4 tick items here **in the same commit as the work**, so a re-run after a cut-off
resumes from the first unticked box._

The four-session campaign was meant to cover the whole product. Sessions 1 and 2 went deep in their
slices, but a review of the whole repository on 2026-10-12 found work that no session prompt named, or
that a session handed on: half the coverage ledger is still `smoke` or `untested`, several features
are dead or half built, and CI has been red on most runs for a week without anyone noticing. This map
gives every one of those items an owner. Nothing here is optional: an item is closed when it is done
with evidence, or moved to `BLOCKERS.md` with the reason and what would unblock it.

## 1. Where things stand

| | |
|---|---|
| Sessions 1 and 2 | Merged. 38 defects fixed (F-236 to F-255, F-257 to F-274); decisions N-040 to N-056. |
| Coverage ledger | 1,411 rows: 691 `deep`, 367 `smoke`, 344 `untested`, 9 `blocked`. |
| Session 3 owns | 145 open or missing rows (79 endpoints, 16 pages, 16 nav items, 15 integrations, 13 UI actions, 6 jobs). |
| Session 4 owns | 575 open or missing rows (282 config variables, 163 migrations, 36 endpoints, 26 nav items, 25 pages, 17 integrations, 16 jobs, 10 UI actions). |
| Code with no ledger row | 4 endpoints, 2 migrations, 3 job types (`campaign_slices.py --missing`). |
| CI | Runs on every pull request (214 runs so far). Red on most runs since 2026-10-06, including `main` right after PR #19 merged (pytest) and the last two completed runs of PR #20 (browser tests, then pytest). See F-275. |

Live numbers, any time: `python docs/hardening/tools/campaign_slices.py`. Who owns a row is decided
by the rules at the top of that tool, not by judgement: session 3 owns identity, the organization,
workspace and platform consoles, token links, notifications, email and security; session 4 owns the
shell, the engine pages and item actions session 2 handed over, the billing journeys, configuration,
migrations, integrations, operations and certification.

## 2. Rules for both sessions

1. **Your session is finished only when** `python docs/hardening/tools/campaign_slices.py --check 3`
   (session 4, the last one: `--check all`) exits 0, every box in your section below is ticked (or the
   item is in `BLOCKERS.md` with its reason), and **CI is green on your pull request's last commit**.
   Until then, keep going; if the context runs out, the owner re-runs the same prompt and you continue
   here.
2. **CI: read check runs, not commit statuses.** GitHub Actions reports through check runs:
   `gh pr checks <number>`, or `gh api repos/sabarishwarne2001/flowpilot-ai/commits/<sha>/check-runs`,
   then `.../check-runs/<id>/annotations`. (Sessions 1 and 2 looked at the commit status API, saw
   nothing and wrote "no CI runs on this repo's PRs"; CI had in fact run, and failed.) Since this
   map's pull request, a red pytest or browser job names each failing test in an annotation and in
   the job summary (`.github/scripts/annotate_test_failures.py`).
3. **`blocked` must be true.** A row is blocked only when no local stand-in exists. Stand-ins that do:
   the fake gateway fixtures of the ARCH-15 seat and billing tests; signed test webhooks built in the
   test (Stripe and Dodo signatures are HMACs over the body); `backend/scripts/erp_mock_targets.py`
   (QuickBooks, Zoho, Business Central, S/4 and SFTP targets); the local MinIO for the S3-bundle
   warehouse; the model stand-in (`E2E_LLM=1`) and its `/__stats` and `/__fail` endpoints; the mail
   sink; a test SAML identity provider that signs its own assertions (`signxml`, already a backend
   dependency, with a self-signed certificate); SCIM driven directly against `/scim/v2`. Rows that need a real customer
   account (BigQuery, Snowflake, Databricks, live Groq or Gemini, live Stripe) stay blocked **with
   that reason** and go on the owner's spot-check list.
4. **Add the missing rows.** Anything `--missing` lists gets a ledger row in your first ledger commit.
5. **Ledger evidence from test runs:** `ENGINE_ROUTE_LOG` / `API_ROUTE_LOG` plus
   `docs/hardening/tools/coverage_from_engines.py`, as session 2 did.
6. **Backend tests locally:** point the app at the test database too
   (`POSTGRES_DB=flowpilot_test pytest …`), or the worker gate reads the development database.

## 3. Session 3: consoles, identity, settings truthfulness, security

### 3.1 First: make CI trustworthy (it gates both remaining pull requests)
- [x] Open this session's pull request early, let CI run, read its annotations, and fix every failing
      or flaky test with the evidence rule. At least one test is flaky: PR #20's last commit and `main`
      after its merge have the identical tree (`cfd2590`), and pytest failed on the first and passed on
      the second. Known candidate: F-256, the late-usage rollup test that fails when it runs at
      minute 59 (reproduction in HANDOFF.md, "Session 1 → later sessions"; the fix is in how rollup
      windows are sealed). Fix it here if it is what turns CI red; otherwise it stays with session 4.
      _Done (session 3): PR #22's full CI (pytest, browser, migrations, frontend, encodings, audits)
      was green on every complete run of this session (`9d426ebe`, `1b17ec9b`, `155b6f93`); F-256 did
      not turn it red, so it stays with session 4._
- [x] A flaky test is a defect: fix the cause (ordering, time, shared state), never retry or skip it.
      _Session 3: the one intermittent failure (`14-configuration`, profile name, failing on a fresh
      database and passing on reruns) was a real defect, not chance: F-318, fixed at its cause._

### 3.2 Carried over from sessions 1 and 2 (HANDOFF.md)
- [x] **Other non-human principals (F-271 follow-up):** confirm partner-portal tokens, SCIM tokens and
      the public gateway's own key check each reach only their own routes (default deny, like N-056).
      _Done (session 3), no defect: `backend/tests/security/test_non_human_principals_default_deny.py`.
      The partner portal has no token of its own (partner members are signed-in people)._
- [x] **Access screens name people:** `NoAccess` and `AccessRestricted` still say "Contact an
      organization owner"; use `useAskPlanOwners` where the viewer is a member.
      _Done (session 3): `useAccessContacts` names the workspace's administrators or the
      organization's owners/admins (who can grant access; plan owners are a different question);
      `frontend/e2e/tests/41-notifications.spec.ts`._
- [x] **Explicit workspace grant for assignment:** organization owners and admins without one cannot
      be assigned review items or obligations. Decide (likely: effective access, as F-249 did for
      calendar feeds) and implement.
      _Done (session 3): N-063, F-296; revoked people also release what they held._
- [x] **Per-tenant request limit for browser sessions:** rate limits are per IP and per API key only.
      Decide whether one is needed; implement if so.
      _Done (session 3): N-061 decided per person, no organization ceiling; F-286 built and proved._
- [x] **The trial's screens, settings and emails (N-052, N-053):** write the wording of the two trial
      emails (session 3 owns it); check the banner, the dismissible "Continue read-only" notice and
      every console a read-only organization can still use (members, archive, export, billing).
      _Done (session 3): the two emails' wording reviewed and kept (escaped and one-line subjects
      since F-285); banner and notice in `e2e/tests/33-free-trial.spec.ts`; consoles in
      `test_free_trial.py` and `test_trial_ended_consoles_stay_usable.py` (F-297 fixed)._
- [x] **Platform consoles:** RevOps (10 endpoints only `smoke`), Unit economics, Sovereign and the
      audit explorer, as an operator, end to end. Session 1 checked the money numbers, not the consoles.
      _Done (session 3): RevOps routes driven for their effect in `test_revops_console_live.py` and
      `26-phase3-consoles` (F-312, F-315); Unit economics, Sovereign and the audit explorer in
      `47-consoles` (F-311, F-313, F-314, F-316, F-317); the operator's egress test and licence
      install in `test_console_routes_take_effect_live.py`. `campaign_slices --check 3` exits 0._

### 3.3 Dead or unbuilt logic found in the 2026-10-12 review
- [x] **Billing and limit emails do not exist.** Dunning steps, "payment failed", "subscription
      ended" and plan changes reach people only as in-app notifications
      (`dunning_service._notify` → `organization_notification_service.emit`, channel `IN_APP`), so an
      owner who does not sign in never hears that a payment failed. Nothing emails anyone at 80% or
      100% of a plan limit. The 19 templates in `backend/app/templates/emails/` cover accounts,
      invitations, ownership and the trial only. Decide and build: billing contacts and owners
      emailed for dunning and limits, through the existing mail and outbox path, honouring the
      notification preferences below. (Session 4 then render-checks them.)
- [x] **Notification preferences do not exist** (only the inbox endpoints). Decide: per-user email
      preferences for non-essential notifications; security and billing mail always sent.
- [x] **Organization-wide "require two-factor"** was deferred to after launch (N-017). Enterprise
      buyers ask for it on the first call; decide again and build it if small.
      _Done (session 3): N-060, built on every plan; `tests/engines/test_require_two_factor_live.py`,
      browser journey in `e2e/tests/40-identity-security.spec.ts`._
- [x] **`idp_session_sync`** is stored and shown but read by nothing (the identity console says so in
      a comment). Wire it or remove it. _Done (session 3): removed (N-058, migration s3a1)._
- [x] **Workspace date format, time zone, currency:** `date_format` is read only as a parsing hint by
      procurement matching (ARCH-30 A2); no screen, email, export or feed formats a date with it, and
      the frontend has 87 ad-hoc `toLocale*` / `Intl` calls. Build the one formatting layer the
      session 3 prompt describes.
      _Done (session 3): N-064, F-298; `frontend/src/utils/displayTime.ts` and
      `backend/app/core/display_format.py`; `npm run check:self` keeps it the only layer._
- [x] **F-124 (unverified):** the SCIM token HMAC may fall back to a constant. Prove or close.
      _Done (session 3): proven and fixed (`tests/services/test_scim_token_key_is_secret.py`)._
- [x] **F-117:** the step-up "Confirm it's you" dialog has no test (`auth.step-up` row).
      _Done (session 3): `e2e/tests/45-step-up.spec.ts`; it also found F-303._
- [x] **Data residency:** the region setting exists; check whether it decides where anything is
      stored, and if not, say so truthfully on the screen (N-029).
      _Done (session 3): it pins export archives and branding files only; the card now says so and
      lists what stays where the deployment runs (F-301, N-066,
      `tests/api/test_residency_says_what_it_pins.py`, `e2e/tests/44-data-residency.spec.ts`)._

### 3.4 Ledger rows (145): `campaign_slices.py --session 3`
Largest groups: partner portal 14 endpoints + the page and nav item; SCIM 13 endpoints (12 never
tested); RevOps 10; identity admin 9; SAML 5 (never tested); auth 4; webhooks 4; branding 3; the
`/confirm-email-change`, `/auth/sso/complete`, `/request/:token`, `/no-access` and `/admin` pages;
15 integrations (SAML, OIDC, SCIM, DNS, platform and tenant SMTP, outgoing webhooks, public API,
warehouses, BYOK providers, iCal feeds, marketplace signing); jobs `notification.deliver`,
`cron:arch07` (audit retention, deleted-file reclaim, email-change expiry), `cron:arch09` (outbox
retention), `cron:identity`, `cron:invitations`, and `trial.reminders` (no row yet).

### 3.5 The session 3 prompt's own deliverables
- [x] `docs/hardening/SETTINGS-LEDGER.md`: every setting, its reader, its effect, its test.
      _Done (session 3): with F-299 to F-308 fixed on the way; its last section lists what is still
      unproven._
- [x] `docs/hardening/SECURITY-REVIEW.md`: the ASVS level-2 pass, including the IDOR sweep over all
      621 operations, SSRF, uploads, tokens, enumeration, browser security, audit and the git-history
      secret scan (`docs/hardening/tools/scan_history_for_secrets.py`).
      _Done (session 3): 20 defects fixed (F-276 to F-295), no cross-tenant leak in the sweeps._

## 4. Session 4: experience, performance, operations, certification

### 4.1 Carried over from sessions 1 and 2 (HANDOFF.md)
- [ ] **Engine item actions with only refusal evidence** (30 endpoints): assertions (rule detail,
      simulate, resolve, delete a clause check), case files (detail, remove a document, close, revoke a
      request, template update and retire), corroboration (refresh, review, page image, delete),
      extraction memory edits, packet splits (start, boundaries, reject), process proposals (detail,
      approve, reject, undo), redactions (page image, region), the ERP target credential, a failed
      posting's retry and accept, the autonomy resume, one notification's detail. Pattern:
      `backend/tests/engines/test_item_actions_*.py`.
- [ ] **Live collaborative review** (`WEBSOCKET …/review/collab/live`, never tested): presence, two
      reviewers editing one field, a reviewer losing access mid-session.
- [ ] **Engine page journeys:** 25 pages and 26 navigation items are proven to render, not to work
      (Entities, Entity 360, packet splits, corroboration runs, obligations, ERP postings and targets,
      process proposals, three-way comparison, assistant canvas, notifications, legacy redirects).
- [x] **Refunds are not recorded:** a Stripe `charge.refunded` leaves the invoice "paid" and RevOps
      revenue unchanged. Decide (Tier-1: a credit note and negative revenue) and implement if small.
      _Done (session 4): N-069, F-327; migration `s4a1_invoice_refunds`;
      `backend/tests/services/test_refunds_are_recorded.py`._
- [x] **F-256 (Low), if session 3 did not fix it in §3.1:** late usage recorded into an hour that had
      no earlier usage is not disclosed as late (the rollup window is never sealed).
      _Done (session 4): every seal pass opens and seals the empty hours behind its cutoff
      (`backend/tests/services/test_late_usage_into_an_empty_hour.py`, minute 59 failed before)._
- [x] **A second subscription from an old checkout tab** still dead-letters and is not refunded:
      alert on it and write the refund playbook in RUNBOOK.md.
      _Done (session 4): F-331 (not written, settled as ignored, the owner emailed at once), N-070
      operator alerts, RUNBOOK §9.8 refund playbook._
- [ ] **In-app plan change:** stays in the billing portal (N-050; it cannot be verified without live
      gateway keys). Confirm every "change plan" screen leads there.
- [ ] **Bulk delete of 500 documents** behind the production proxy: time it; if it nears the gateway
      timeout, move it onto the existing `work_items.bulk` job.
- [ ] **Dormant handlers** `batch.expand_archive` (zip intake is not offered) and `work_items.bulk`:
      keep blocked with that reason, or remove the dead code.
- [ ] **Blocked rows that a stand-in can unblock:** `billing.seat_sync`, `billing.assemble_invoice`,
      `billing.reconcile`, `usage.reconcile` and the `worker-loop:stripe` loop (fake gateway, signed test
      events); `erp.deliver_posting` (`erp_mock_targets.py`). They were blocked as "session 1's slice";
      session 1 is finished.
- [ ] **The one-week trial journey** in the browser: sign up → trial → reminder → trial ends →
      subscribe → everything back (N-052; HANDOFF "Owner request").
- [ ] **Browser-suite isolation:** keep new tests from signing a shared persona out everywhere.

### 4.2 Operations gaps found in the 2026-10-12 review
- [ ] **Host cron sweeps never ran against the compose stack.** 17 lines in `backend/deploy/cron.d/`
      (and the compose backup lines) run only from the host's cron (RUNBOOK §9.6), and the ledger's
      `cron:*` rows say "not run by docker-compose". Session 2 proved the 23 schedules inside the
      worker, not these. Run each line by hand against the compose stack (or the stack the runbook
      builds) and prove it succeeds; that includes the retention purge, ERP posting retries,
      obligations, invitations and every backup and drill.
- [ ] **Configuration (282 rows):** one contract test that every `Settings` field is read by code
      (otherwise remove it), documented in `docs/CONFIGURATION.md`, present in `.env.example` and
      `.env.production.template` where a deployment needs it, and refused at boot when a required
      secret is missing. Closes F-011's long tail.
- [ ] **Migrations (163 rows):** one whole-chain proof on a fresh database: upgrade to head,
      downgrade to base, upgrade again. Add rows for `p9a1_truthmesh_engine` and
      `s2a1_free_trial_clock`. Review F-017's 283 undeclared index and constraint lines together
      with the index work in the prompt's section B.
- [ ] **Integrations (17 rows):** mark each `deep` with the test that exercises it, or `blocked` with
      the reason (live Stripe, Groq, Gemini: owner spot checks).
- [x] **CI health:** pytest takes ~1 h 40 min on GitHub, because the security and engine tests run
      twice (in the "Phase 2" and "Phase 4" proof steps, then again in the full suite). Remove the
      duplication or shard the suite so a red run is known in minutes (F-030). Make the browser job a
      required check once it is stable.
      _Done (session 4): the proof steps are gone and the suite runs once in four parallel shards
      (`PYTEST_SHARD`, `tests/infra/test_ci_shards_cover_the_suite.py`); no `--maxfail`, so a red
      shard names every failure; the browser job is in the aggregate `CI` check's `needs`; the
      migrations job also runs the whole chain to base and back (F-330)._
- [ ] **No container pulls in the sandbox** (F-006, F-040): if Docker cannot pull images, run
      RUNBOOK §3 by hand on a clean environment, validate the compose files statically, and put the
      compose-only steps on the owner's checklist with exact commands.
- [ ] **Load test:** use Locust (installs from PyPI); k6 needs a download that may be blocked.
- [ ] **Bundle size:** measure before splitting. The "~412 KB gzipped" figure is from an older pass;
      the build warns that the main chunk is over 1,000 kB; 54 routes are already lazy-loaded, so the
      weight is elsewhere (shared vendor code, `manualChunks` in `vite.config.ts`).
- [ ] **Older findings still open:** F-006 (a real compose run on a server), F-045 (CPU-only torch
      image), F-050 (its "re-run pending" note is stale: close it), F-030 (suite speed).
- [ ] **Tailwind 4:** five high `npm audit` advisories (build-time file watching in Tailwind CSS 3,
      nothing shipped) are accepted until the Tailwind 4 move (FINDINGS "Dependencies"). It rewrites
      the CSS configuration; do it only once everything above is green, with before/after screenshots
      of every page, or record it as accepted for launch.

### 4.3 Public face
- [ ] There is no public pricing page, and no terms or privacy page. Build them as the session 4
      prompt says (pricing from the plan source of truth; legal pages marked for the owner's review).

### 4.4 Certification
- [ ] `python docs/hardening/tools/campaign_slices.py --check all` exits 0 before a GO verdict.
- [ ] The owner launch checklist (seed below) is complete, with exact steps.

## 5. Owner launch checklist (seed; session 4 completes it)
Only the owner can do these. Each comes from an earlier decision or finding:
1. Payment gateway and prices: Stripe test mode first, then live; one per-seat monthly price per paid
   plan in `GATEWAY_PRICE_ID_*` (N-026); the dashboard webhook endpoint and its signing secret (F-125).
2. Stripe customer portal: allow switching between Developer, Business and Enterprise, with proration
   (N-050); the same on Dodo if Dodo is used.
3. Yearly prices: Developer $490 and Business $2,990 per seat per year, if a yearly switch is wanted (N-051).
4. Postmark for transactional mail, with the SPF and DKIM records for `flowpilot.ai` (N-027).
5. DNS: an A record for `app.flowpilot.ai` before Caddy's first start; a readable `admin@flowpilot.ai` (N-028).
6. Rotate every key ever pasted into a chat or committed (session 3's history scan lists them).
7. On every deploy: re-run `seed_quota_tiers.py` and `seed_price_book.py --version auto` (RUNBOOK §9.3).
8. Before launch: spot-check Groq's and Google's prices against `seed_price_book.py` (N-054), and run
   the real-model checks sessions 2 and 3 list (prompt injection, line items, the GST invoice,
   photographed and handwritten pages).
9. Pin images by digest at the first deploy (N-017); build the CPU-only torch image on a machine that
   can reach download.pytorch.org (F-045).
10. Review the legal pages session 4 drafts; merge the last pull request; the first deploy (RUNBOOK §9).
