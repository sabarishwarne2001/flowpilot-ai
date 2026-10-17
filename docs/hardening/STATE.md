# Hardening campaign — STATE

_FlowPilot AI was renamed Servabo on 2026-10-16. Entries below that date keep the old name._

_Last updated: 2026-10-17 (Helm visual session)_

## Current phase — Helm visual session: Night Bridge, the switch, Lumen and the website's Helm page
Branch `claude/beautiful-dijkstra-yvlspo` (the session's assigned branch), based on `main` at `5e4366d`
(PR #25 merged). PR sabarishwarne2001/servabo#26. Founder authority on design: decisions in NEEDS-OWNER.md as
`DECIDED (Helm visual session)` (N-111 to N-116). The owner also asked for Documents to be upgraded in
its own style (Lumen, N-113). **If cut off, paste the prompt again: continue on this branch and PR from
the first unticked box. Never restart.** All boxes are ticked: the session is done; next is the owner's review of PR #26.

- [x] 0. Start-up: docs read; stack up (Postgres 16 + pgvector 0.8.0 on :5433 e2e and :5434 backend
  tests, Redis, API, worker, model stand-in); `seed_website_demo.py`; "before" shots of every Helm page
  and Documents (light, dark, 1440, 390; scratch only); Helm chunks measured (58.2 kB gzipped).
- [x] 1. Night Bridge design system (`styles/helm/`, `components/helm/ui/`, `docs/helm/VISUAL.md`).
- [x] 2. Rupees in Indian grouping in Helm (F-462, self-check failed first).
- [x] 3. Read-only promise calendar API and the ledger's due-day filter (backend tests failed first).
- [x] 4. Helm shell; Start no longer blinks before a book is chosen (F-463, spec 85 failed first).
- [x] 5. The switch and its view transition (F-464: the router's option is dropped; the switch starts it).
- [x] 6. Cockpit: KPI strip, fan chart, Promise calendar, pill bars, instruments, plan; spec 85.
- [x] 7. Plan, Mandates, ledger, Time machine (F-466), Bank, Certificates, Settings, Start, first run,
  tour, preview; F-467 (₹1.5T).
- [x] 8. Documents: Lumen.
- [x] 9. Website: Helm page in Night Bridge, switch demo, glass frames, `helm-certificate` rename and a
  true `helm-plan` shot, every screenshot recaptured as an Indian reader.
- [x] 10. Records: VISUAL.md, FLAGSHIP identity, NEEDS-OWNER, FINDINGS, COVERAGE (`--check all` exits 0),
  HANDOFF.
- [x] 11. Full browser suite (808 passed, 2 skipped, 0 failed, on 17f3896) and website checks.
- [x] 12. Owner's added task: the Documents Overview redesigned in Lumen (N-117), with two read-only
  overview fields (documents read, the last 14 days; test failed first).
- [x] 13. The Overview's specs re-run (10, 21, 22, 72, 74, 80, 85: 335 passed, 2 failed; spec 10's
  label fixed and re-run green; spec 22's is the pre-existing order dependence in HANDOFF).
- [x] 14. Finish: CI green on 0da2906 (all 11 checks, browser suite included), PR marked ready for review.

**Checkpoint (2026-10-17).** Helm specs 78–85 pass on the new look (axe in both themes; no sideways
scroll at 1440, 1024, 768, 375). Spec 85: six tests, including the cockpit ready within 1.5 s and no
long task over 200 ms after it. Backend: `tests/services/helm` and the route walkers 486 passed; the
role matrix 4 passed. Frontend: lint, typecheck, e2e typecheck, `check:self` clean. Bundle: Helm's lazy
chunks 59.0 → 76.1 kB gzipped (+17.1, allowance 35); the main stylesheet 23.5 → 24.8 kB gzipped (Helm's
CSS is in it because the switch sits in Documents' header). No new runtime dependency.
Backend (tests/api, core, security, services/helm and the Helm live tests): 2,206 passed, 11 skipped.
Website: typecheck 0 errors, 18 pages, 1,296 links, HTML valid, axe clean in 72 page views, Lighthouse
97/97/96 (other categories 100).

## Previous phase — Website and payments session: Servabo, the website and Dodo Payments
Branch `claude/new-session-3qnrpb` (the session's assigned branch), based on `main` at `906c921`
(PR #24, the flagship session, is merged; `docs/product/FLAGSHIP.md` and HANDOFF "Flagship session →
website and payments session" are present). The owner gave full founder authority: decisions are
built and recorded in NEEDS-OWNER.md as `DECIDED (website and payments session)`; the brief's own
decisions as `DECIDED (owner, planning chat, 2026-10-15)`. **If this session is cut off, paste the
same prompt again: continue on this branch and its open pull request, from the first unticked box
below. Never restart.**

**Plan (the brief's order A → B → C → D; each defect proven by a failing test first):**
- [x] 0. Start-up: docs read; live stack up (Postgres 16 + pgvector 0.8.0 on :5433 e2e and :5434
  backend tests, Redis, API, worker `--loop all --profile all`, model stand-in, production bundle
  with CSP), logs to files; this plan; draft PR.
- [x] A1. Rename what people see: UI strings, titles, meta, manifest, emails, PDFs and exports,
  calendar feed, redaction stamp, samples, notifications, OpenAPI title, README and current docs.
- [x] A2. Code identifiers (`FlowPilotError` → `ServaboError`, …), pure renames.
- [x] A3. Protocol identifiers (signature header, TXT record, WebSocket subprotocol, refresh
  cookie, key prefixes, local-storage keys), each listed in CHANGELOG.md.
- [x] A4. Database-level names in a new Alembic migration (the audit sweeper setting).
- [x] A5. Configuration `FLOWPILOT_*` → `SERVABO_*` with a one-release alias and one startup
  warning; database defaults and templates `servabo`, `servabo_e2e`, `servabo_test`.
- [x] A6. Operations: Docker names, cron files `servabo-sweepers`, `/etc/servabo/`, backup names,
  dev scripts, CI names, the e2e seed.
- [x] A7. History lines at the top of FINDINGS, NEEDS-OWNER, STATE, CHANGELOG.
- [x] A8. Brand assets: logo mark and wordmark (SVG), favicons, sign-in screen, emails, Open Graph.
- [x] A9. Guard test: the old name fails outside an explicit allowlist.
- [x] A10. Part A checkpoint: full backend suite, browser suite, frontend checks green.
  Checkpoint (2026-10-16): browser 825 passed, 1 skipped (1,472 jobs SUCCEEDED); backend 5,064
  passed with the 8 sandbox-only failures main also has, plus 4 the rename caused, now fixed with
  tests (old API key prefix, old-name passwords, a half-renamed test, a storage-boundary entry).
- [x] B1. `website/`: Astro + TypeScript + Tailwind on shared brand tokens, self-hosted fonts, no
  third-party scripts; layout, header, footer with every Dodo-required link.
- [x] B2. Pricing data: `backend/scripts/export_public_pricing.py` → `website/src/data/pricing.json`,
  drift test, build fails on an unlabelled plan.
- [x] B3. Pages: home, Helm (flagship), product, four solutions, pricing, security, about, contact.
- [x] B4. Legal: terms, privacy, refunds, acceptable use, subprocessors (data file from the code),
  cookies; 404, sitemap, robots, canonical, OG/Twitter, structured data.
- [x] B5. Real screenshots (`website/scripts/capture-screenshots`), light and dark.
- [x] B6. `_headers` (strict CSP, HSTS…), Caddy site block, `docs/website/DEPLOY.md`,
  `docs/website/DODO-REVIEW.md`.
- [x] B7. Joining to the app: `WEBSITE_URL` / `VITE_WEBSITE_URL`, app `/pricing` `/terms`
  `/privacy` redirect, links back, email footers, Billing → refunds link, `--website` dev switch.
- [x] B8. CI `website` job (install, typecheck, build, links, axe in both themes, HTML validation,
  Lighthouse ≥ 95 on home, pricing, Helm), required.
  Checkpoint (2026-10-16): 18 pages build; 1,275 links resolve; HTML valid; axe clean in both themes
  at 360 and 1280 px; Lighthouse 97/100/100/100 on home, pricing and Helm; `npm audit` 0. Browser
  specs 70, 72, 74 (signed out), 30, 01, 60 pass against the new bundle.
- [x] C1. `backend/tests/fakes/fake_dodo.py` (HTTP stand-in + Standard Webhooks signer);
  `scripts/e2e_stand_in.py --gateway dodo`; browser journeys on Dodo by default; Stripe journeys kept.
- [x] C2. Default gateway DODO everywhere; production guard (key, secret, live/test agreement).
- [x] C3. Reconciliation behaviour tests for every handled Dodo event, ordering, replay, grace,
  add-ons, customer adoption and racing checkouts; one live concurrency test.
- [x] C4. Dodo payments as invoice rows, PDF download route, merchant-of-record note.
- [x] C5. Refunds and disputes (record, notify, RevOps, lost dispute → read-only).
- [x] C6. Plan changes with Dodo's preview (amount and date first), upgrades now, downgrades at
  renewal, downgrade checks first; portal for payment method.
- [x] C7. Seats on Dodo proven end to end (`DODO_PREVIEW`).
- [x] C8. Overage capability: `ALLOW_AND_BILL` published as `REFUSE` without usage billing; copy.
- [x] C9. Customer details reach Dodo (or the page says where); never Stripe on a Dodo account.
- [x] C10. No Stripe where a customer looks; worker loop `billing` (alias `stripe`).
- [x] C11. Yearly products on Dodo; switch only when configured; website reads the same data.
- [x] C12. Dodo fees in unit economics and the margin console; ≥ 55% at ceilings after fees.
- [x] C13. Checkout return waits for the webhook, with a timeout message.
- [x] C14. `backend/scripts/dodo_setup.py` (test mode only); `docs/billing/DODO.md`.
  Checkpoint (2026-10-17): Part C done. Browser journeys bill through the stand-in Dodo by default
  (start-stack.sh; E2E_GATEWAY=stripe keeps the Stripe journeys, also run in CI): 60 pays on Dodo's
  checkout page and the return page confirms it (C13 proven), 62 buys seats through Dodo's preview,
  64 runs dunning on a held renewal and fixes the card in Dodo's portal, new 68 covers an upgrade at
  Dodo's price, the Dodo invoice download, a scheduled downgrade withdrawn, a refund and a portal
  cancellation; 60–67 also pass on Stripe. Found and fixed on the way: held Dodo renewals turned
  read-only silently (billing.hold_notices, migration w1a4); an upgrade was undone by Dodo's own
  webhooks (plan now from the billed product); two order-dependent tests. Live concurrency test
  (c3174eb); dodo_setup.py and docs/billing/DODO.md (C14).
- [x] D1. RUNBOOK §9 rewritten; CONFIGURATION.md and TENANCY-AND-PLANS.md regenerated (both were
  already current).
- [x] D2. HANDOFF: owner launch checklist rewritten (12 steps); "Website and payments session → next sessions".
- [x] D3. Finish: full suites, logs clean, ledgers, PR ready, final message.
  Checkpoint (2026-10-17): backend 5,162 passed, 11 skipped, 1 failed in four local shards (the
  stand-in test since fixed in 23b63dd, 3/3 passing); browser 802 passed, 2 skipped on a fresh e2e
  database, billing through the stand-in Dodo, production bundle, CSP enforced; Stripe journeys
  60–67 and spec 20's Billing group pass with E2E_GATEWAY=stripe. Logs: 0 tracebacks, 0 ERROR, one
  5xx (spec 31's expected 503); 1,465 jobs SUCCEEDED; 62 Dodo events PROCESSED. tsc, e2e typecheck,
  lint, check:self, build, no source maps; website typecheck, build, links, HTML, axe, Lighthouse
  97/97/96; export_public_pricing --check current; Alembic one head (w1a4_hold_notices), up / down
  to base / up; pip-audit no known vulnerabilities; npm audit --omit=dev 0 (app and website).
  Ledgers: F-444–F-461, N-103–N-110, COVERAGE rows.

## Previous phase — Flagship session: Helm, the flagship engine
Branch `claude/new-session-yviysi` (the session's assigned branch), based on `main` at `5e27687`
(PR #23, campaign session 4, is merged). Draft PR titled `Helm: the flagship engine`. The owner gave
full founder authority: decisions are built and recorded in NEEDS-OWNER.md as
`DECIDED (flagship session)`; the brief's own decisions as `DECIDED (owner and planning chat,
2026-10-15)`. The design is `docs/helm/ARCHITECTURE.md`. **If this session is cut off, paste the same
prompt again: continue on this branch and its open pull request, from the first unticked box
below. Never restart.**

**Build checklist (the brief's §12 order; each defect proven by a failing test first):**
- [x] 0. Start-up: docs read, engines studied, live stack up (Postgres 16 + pgvector 0.8.0 on
  :5433 for e2e and :5434 for backend tests, Redis, API, worker `--loop all --profile all`, model
  stand-in), `docs/helm/ARCHITECTURE.md` written, draft PR opened.
- [x] 1. Skeleton: migration `h1a1_helm`; payables ledger, settlement and cash; mandates 1, 2, 5, 7;
  the MILP and the Guard with certificates; `helm.plan`; mode switch, minimal cockpit, approving one
  action; one browser test (`78-helm-skeleton`). Push, checkpoint here, whole backend suite (run:
  4,576 passed, 5 failed: all five were Helm breaking platform contracts, fixed and re-run green).
- [x] 2. Widen the ledger: receivables, POs and deliveries, discounts, notices, compliance
  documents, MSME detection, counterparty behaviour (built with the skeleton; consistent snapshot
  reads proven live).
- [x] 3. The rest of the library (3, 4, 6, 8–12) and the simulation; hold releases and waivers.
- [x] 4. Time machine (backtest, forecast, value held) and the sample company
  (`backend/scripts/helm_demo_company.py`): its backtest finds exactly what was planted, to the paisa.
- [x] 5. Sentence drafting, readback, the validator, the gallery, autonomy and caps, approvals
  (`POST …/mandates/draft`, the Mandates composer and editor, spec 79; the Time machine page too).
- [x] 6. Experience: full cockpit and visual identity, first run, Monday briefing email, preview and
  upsell, the Overview card (instrument trends, the flight path scrubbing into a replay's past,
  `/helm/setup`, the sample company button, the Overview card, plan-card limits; specs 78–80).
- Owner additions (2026-10-15, after box 6), built before box 7:
  - [x] A. Bank statement import: CSV or Excel with a column mapping saved per bank account; cash
    positions; automatic matching of payments to scheduled or approved invoices (amount, date
    window, reference or payee) so "paid" and "value held" rest on evidence; unmatched lines listed.
    (`h1a2_helm_bank`, ARCHITECTURE §16, spec 81. An adversarial review found 34 issues: memory
    and zip-bomb limits, workspace read rules, rejected matches re-made, loose payee matching,
    newest-first balances, discount windows, value events surviving an undo, …; each was proven by
    a failing test and fixed before the first commit.)
  - [x] B. Supplier statement reconciliation mandate: monthly, every supplier statement against the
    Promise ledger, differences with evidence, a drafted reconciliation email. (Found and fixed
    first, proven by a failing test: a supplier's statement of account was read as a bank's, so it
    marked their invoices PAID and counted what we owe them as our cash. `h1a3_helm_reconcile`,
    library mandate 13, ARCHITECTURE §17, live test on the sample company's planted statement.)
  - [x] C. A guided Helm tour on the sample company (instruments, plan, Guard certificate, Time
    machine) for demos and first-time users. (ARCHITECTURE §18, spec 82.)
  - [x] D. If time remains: more hardening tests and a performance run at 10,000 documents.
    (`tests/performance/test_helm_budgets.py`, ARCHITECTURE §13: the plan, the simulation and the
    reconciliation are within budget at 10,000 documents; the ledger update after one change is
    not, 2.9 s against 1 s, above about 2,000 documents a workspace: next step in HANDOFF. The run
    found F-423, F-424 and F-425. Spec 83 checks every Helm page for accessibility and widths.)
- [x] 7. Should-ship: custom condition mandates, approval chains, backtest PDF, what-if, webhooks and
  API scopes. (ARCHITECTURE §11, §14.6, §19–§21; N-098 to N-102; spec 84 walks what-if, a custom
  condition and the approval chain, spec 80 downloads the replay PDF. Found and fixed, each proven
  by a failing test first: F-429 to F-437, among them an API key that opened every organization its
  issuer belonged to (F-435, High) and a payment Helm approved in Act mode that still ran after an
  approval chain was saved (F-438).)
- [x] 8. Hardening: isolation sweep (user and API key), role matrix, double clicks and concurrency,
  worker gates, performance budget, accessibility and widths, logs clean.
  (`test_helm_role_matrix_live.py`, `test_helm_api_scopes_live.py`, `test_helm_concurrency_live.py`
  (every Helm write sent 3 to 8 times at once), `test_helm_worker_gates_live.py`, the 10,000-document
  budget (§13), spec 83. The concurrency and gate tests failed 23 times before their fixes: F-439
  to F-441. COVERAGE.csv has a row for every Helm route, page, job and migration.)
- [x] 9. Finish (§13): full backend and browser suites, frontend checks, one Alembic head with
  up/down/up, live proof on the sample company, `docs/product/FLAGSHIP.md`, HANDOFF sections,
  `docs/helm/MANDATES.md`, NEEDS-OWNER, FINDINGS, COVERAGE, PR ready, final message.

**Progress (2026-10-15, after box 6):** boxes 1–6 done and pushed; CI green on c2b87d3. Fixed, each proven by a
failing test first: a plan could read half of a ledger rebuild; a failing hand left its action
stuck EXECUTING; undo lost a settlement's evidence; Helm's notice switches were shown to members and
the briefing could not be turned off; `helm_plans.conflict` stored JSON null; the dispute email
crashed the escape contract; the ledger paged on a non-unique key; the ledger keyed counterparties
by name only (two suppliers with one name merged); the sample workspace counted against
`limit.workspaces`. CI run 260's browser failure was spec 78 assuming which of two invoices the
radar marks (it depends on which the worker reads last): the spec now checks both orders.
Box 5 also fixed, each proven by a failing test first: a gallery mandate with no default (cash
floor, PO threshold) was saved and backtested with the gallery's example as if the owner had chosen
it; editing an active mandate was called a duplicate of itself. Box 6 fixed the Monday briefing's
links (no such page /helm/cockpit; "turn it off" went to the organization's page; the HTML had no
opt-out link). **Next:** owner addition A (bank statement import), then B, C, then box 7.
The full backend suite takes ~45 min on this sandbox; never edit backend code while it runs
(lazily imported modules load mid-run). Restart the e2e stack (`E2E_LLM=1
frontend/e2e/scripts/start-stack.sh`) after backend changes, and run browser specs with
`npx playwright test -c e2e/playwright.config.ts <spec>` from `frontend/`.

## Previous phase — Pre-launch campaign, session 4 of 4: experience, performance, operations, certification
Branch `claude/kind-hamilton-5lpp19` (the session's assigned branch), based on `main` at `6802a2b`
(PR #22, session 3, is merged; HANDOFF.md has "Session 3 → later sessions"). Draft PR titled
`Campaign session 4: experience, operations and launch certification`. Owner delegated founder
authority: decisions are taken, built and recorded in NEEDS-OWNER.md as
`DECIDED (campaign session 4)`. **If this session is cut off, re-run the same prompt: continue on
this branch and its open pull request, from the first unticked box in CAMPAIGN-MAP.md §4.**

**Plan for this slice (priority order; each fix proven by a failing test first):**
1. CI: keep it green on every push; remove the pytest duplication (F-030) and make the browser job
   a required check.
2. Carry-overs that can be wrong money or lost work: F-324 (verify, then fix), F-256 (late usage
   not disclosed), refunds recorded (credit note, negative revenue), the duplicate gateway
   subscription alert and its refund playbook, every "change plan" screen leading to the portal.
3. Operations (§C): configuration contract test and docs, the whole migration chain up/down/up,
   every host cron line run by hand against the stack the runbook builds, backup and timed restore
   drill, error and dead-job alerts for the owner, the Windows scripts, `COSTS.md`.
4. Customer journeys (§D) as Playwright specs against the production bundle with the worker on.
5. Item actions, the live collaborative-review WebSocket, the engine page journeys, the billing
   jobs through the fake gateway, ERP delivery through the mock targets (§4.1 rows).
6. Experience (§A): shell, every page at four widths in light and dark, accessibility (axe-core),
   first run, public pricing/terms/privacy pages, email rendering.
7. Performance (§B): bundle split and Lighthouse, Locust load run, N+1 and indexes, 10,000
   documents, 500-document bulk delete timing; `PERFORMANCE.md`.
8. Ledger to `--check all` = 0, `PRODUCT-NEXT.md`, the launch certification, the owner launch
   checklist, HANDOFF, final suites, CI green, PR ready.

**Progress (checkpoint 1, 2026-10-13):** items 1 and 2 done, item 5 partly. CI is green on every
complete run, pytest now runs once in four shards, the browser tests are a required job and the
whole migration chain goes down to base and up again (F-030, F-330). Fixed and proven, each with a
failing test first (FINDINGS.md, session 4 section): F-324 (a released document ran its rules
never, or twice), F-256 (late usage into an empty hour), F-327 (refunds recorded on the invoice,
negative revenue, the organization told; N-069), F-328 (API measurements written while it runs),
F-331/F-332 (the owner is emailed about dead jobs, unapplied payments, error bursts and a second
live subscription; RUNBOOK §9.8 refund playbook; N-070), F-333 to F-337 (closed cases, clause checks
and reviews, redaction previews, packet plans). Every one of the 30 engine item actions is now used
for real, and ERP delivery over HTTPS runs against the mock target. **Next:** the live
collaborative-review WebSocket, engine page journeys, the billing jobs through the fake gateway,
then operations (§C), the customer journeys (§D), experience (§A), performance (§B), the ledger.

**Progress (checkpoint 2, 2026-10-13):** items 3 (largely), 4 and 7 (bundle) done. All eight customer
journeys are Playwright specs that pass against the production bundle with the worker running
(`frontend/e2e/tests/60` to `67-journey-*.spec.ts`; journey 4, Enterprise, proves the domain over a
real DNS lookup, a SAML sign-in, SCIM, egress lockdown and the audit export). Every host cron line
runs as cron runs it (`backend/tests/operational/test_host_cron_lines.py`) and by hand against the
stack; the encrypted backup restores in a timed drill (RUNBOOK §9.4). The whole migration chain is
proven up, down to base and up. The first load is 191.9 KB gzipped (was 634 KB; Lighthouse
performance 91 on sign-in). Public pricing, terms and privacy pages (N-073). COSTS.md and
PRODUCT-NEXT.md written; the 17 integrations and every migration and job row closed in the ledger.
Fixed and proven since checkpoint 1: F-338 to F-348. **Next:** the configuration contract (282
rows, in progress), the Locust load run, the engine page journeys and the open endpoint, page and
navigation rows, accessibility and widths (§A), the Windows scripts, then the certification,
HANDOFF and the owner launch checklist.

**Progress (checkpoint 3, 2026-10-14 — session close):** all eight plan items done. Fixed and proven
since checkpoint 2: F-349 to F-364 (configuration contract, seeded workspaces ready, the audit sweep,
sealed redaction files, empty assistant answers, old links, the PDF viewer, line-item reviews, every
page WCAG 2.1 AA in light and dark, the reset link, the assistant freezing the API under load, old
document links), F-380 (emails for mail clients) and F-381 (four widths). First run: a
getting-started card with a sample invoice. At the owner's request the session then audited
TruthMesh, Batch Operations and Document Packs in depth: three new browser specs on tenants of their
own (75, 76, 77) and backend hardening tests; fixed F-382 (concurrent mesh builds), F-390 and F-407
(hostile or damaged packages), F-398/F-399 and F-406/F-408 (packs), F-409 (an empty mesh rebuilt on
every poll). Helper sessions proved the eight unproven settings (F-365, a legal name never reaching
Stripe) and fixed F-370 (key rotation missed 14 secret columns), F-371 and F-372. Decisions N-069 to
N-077. The audit's reproduced-but-unproven items, the N-076/N-077 builds and 32 features with
partial browser coverage are in **`docs/hardening/NEXT-SESSION-PROMPT.md`** (paste it into a new
session). `campaign_slices --check all` exits 0; one Alembic head (`s4b1_drop_email_settings`).

## Previous phase — Pre-launch campaign, session 3 of 4: consoles, identity, settings, security
Branch `claude/determined-planck-e5ftrx` (the session's assigned branch), based on `main` at
`3caab78` (PR #21, the campaign map, is merged; HANDOFF.md has "Campaign review before session 3").
Draft PR titled `Campaign session 3: consoles, identity, settings and security`. Owner delegated
founder authority: decisions are taken, built and recorded in NEEDS-OWNER.md as
`DECIDED (campaign session 3)`. **If this session is cut off, re-run the same prompt: continue on
this branch and its open pull request, from the first unticked box in CAMPAIGN-MAP.md §3.**

**Plan for this slice (priority order; each fix proven by a failing test first):**
1. CI trustworthy (CAMPAIGN-MAP §3.1): read the PR's check runs and annotations, fix every red or
   flaky test at its cause.
2. P0 security: non-human principals default deny (SCIM tokens, the public gateway, partner
   portal), F-124 (SCIM token key), the IDOR sweep over all 621 operations as tenant A with
   tenant B's ids (and as an API key, and in an ungranted workspace), privilege escalation and
   mass assignment, SSRF on every customer-chosen URL, uploads, tokens, enumeration, browser
   security, audit; git-history secret scan. Result: `SECURITY-REVIEW.md`.
3. Enterprise identity live: SCIM (13 endpoints) and SAML (5) against a self-signed test IdP,
   the identity console (domains, certificates, dry-run, activation, SCIM keys).
4. Unbuilt logic (§3.3): notification preferences, billing and plan-limit emails, organization
   "require two-factor", `idp_session_sync`, data residency truthfulness, the step-up dialog test.
5. Settings truthfulness: one formatting layer (workspace date format, time zone, currency; the
   person's own profile wins in the app) replacing the ad-hoc calls; `SETTINGS-LEDGER.md`.
6. Carry-overs (§3.2): access screens name people, assignment without an explicit grant, a
   per-tenant request limit decision, the trial's emails and read-only consoles, the platform
   consoles as an operator, the partner portal.
7. Ledger rows (`campaign_slices.py --check 3`), host sweeps, finish checks, HANDOFF, PR ready.

**Progress (checkpoint 1, 2026-10-12):** items 2 (largely), 3 and 4 done and committed; item 1
waiting on the first CI run's pytest job (every other job green). Fixed and proven, each with a
failing test first (FINDINGS.md, session 3 section): F-276 (require SSO enforced nowhere, critical),
F-277 (IP allowlist never read), F-124 (SCIM token key), F-278 (live review WebSocket skipped the
sign-in policy), F-279 to F-282 (SCIM and SSO after a downgrade, read-only or archive), F-283
(invalid IdP certificates accepted), F-284 (partner standing self-declared), F-285 (markup and
header injection in emails), F-286 (per-person rate limits), F-287 to F-290 (billing and security
notices emailed, notification preferences, Stripe announcements, 80%/100% limit alerts, addresses
in logs). Built and decided: N-057 to N-062 (sign-in policy everywhere, `idp_session_sync` removed,
SCIM/SSO after downgrade, require two-factor, per-person limits, which notifications are emailed).
Proven with no defect: non-human principals default deny, IDOR sweeps (attackers B and an
enterprise X against C: 353 reads, 832 writes, 789 as an API key, an ungranted workspace), privilege
escalation as a viewer and as billing, SAML/OIDC attacks, CSV formula neutralisation, image uploads
re-encoded, git-history secret scan (placeholders only). Local full backend suite on the
checkpoint's first half: 3985 passed. **Next:** SSRF probes, tokens, enumeration, cookies/CORS/
redirects, audit immutability, then `SECURITY-REVIEW.md`; the formatting layer and
`SETTINGS-LEDGER.md`; §3.2 carry-overs; ledger rows; CI.

**Progress (checkpoint 2, 2026-10-12, evening):** items 1, 2, 4 and 5 done. CI green on every
complete run (§3.1 ticked). Security review written (`SECURITY-REVIEW.md`, F-291 to F-295). The
display layer is one module per side (F-298, N-064) and `SETTINGS-LEDGER.md` lists every setting
with its reader, effect and test; on the way, settings that changed nothing or said more than they
did were fixed, each with a failing test first: chunking controls (F-299), brand colours (F-300,
N-065), data residency's promise (F-301, N-066), webhook redelivery crashing the worker (F-302),
the transfer form's double password dialog (F-303), audit log retention ignored and a fixed
400-day delete (F-304, N-067, High), the organization mail page (F-305), workspace reply-to
(F-306), AI settings reach (F-307), the identifier's support promise (F-308). Partner portal
routes proven end to end; a partner's rate could never change (F-309) and a new partner could
never be used (F-310). Proven with no defect: egress lockdown, document controls, the step-up
dialog (F-117). Ledger: 88 session 3 rows open (from 145). **Next:** platform consoles as an
operator (RevOps, Unit economics, Sovereign, Audit explorer), the partner portal page and nav,
host crons (arch07, arch09, identity, invitations), remaining ledger rows, HANDOFF, finish line.

**Final checkpoint (session 3 finished, 2026-10-12, night):** all seven items done. Platform
consoles proven as an operator (RevOps, Unit economics, Sovereign, audit explorer: F-311 to F-317);
partner portal page and suspended read-only state (F-319); profile save no longer pins the clock
(F-318, the 14-configuration CI failure); workspace regional settings checked and every zone and
currency offered (F-320, F-321, N-068); the settings page keeps its section (F-322); a public-API
workflow trigger now reaches the engine (F-323, High); erasure by a person's name, not an unseen
user ID (F-325), and member lists no longer fail once someone has been erased (F-326, found by
the full browser run). F-324 (a verification/automation race) is recorded as **unverified** and handed
to session 4. Host crons (arch07, arch09, identity, invitations) proven. New tests: console routes
by effect, custom domains and TLS, warehouse connectors on the wire, DNS lookup, AI settings at the
provider, browser specs 48 (console effects) and 49 (account changes and emailed links).
`campaign_slices --check 3` exits 0 (one row, `branding.domain`, blocked with its reason).
HANDOFF.md has "Session 3 → later sessions". **Next action:** session 4 (CAMPAIGN-MAP §4); the PR
is marked ready once CI is green on its last commit.

## Before session 3 — campaign review (2026-10-12)
A review of the whole repository after sessions 1 and 2 found work no session prompt named or that
a session handed on: 711 coverage rows still `smoke` or `untested`, 9 pieces of code with no ledger
row, dead or unbuilt features (billing and limit emails, notification preferences, the date-format
setting, `idp_session_sync`), host cron sweeps never run against the compose stack, and CI red on most
runs since 2026-10-06 without any session noticing (F-275). **Everything left is assigned, item by
item, in `docs/hardening/CAMPAIGN-MAP.md`; read it right after this file.** Who owns each open
ledger row is computed by `python docs/hardening/tools/campaign_slices.py` (session 3: 145 rows;
session 4: 575, of which 282 configuration variables and 163 migrations). A session is finished
only when `campaign_slices.py --check <its number>` exits 0, every box in its section of the map is
ticked or in BLOCKERS.md, and CI is green on its pull request.

## Previous phase — Pre-launch campaign, session 2 of 4: documents, AI and every engine
Branch `claude/trusting-ptolemy-yiz005` (the session's assigned branch), draft PR #20 opened early.
Based on `main` at `340a8c4` (session 1's PR #19 is merged; HANDOFF.md has its section). Owner
delegated founder authority: decisions are taken, built and recorded in NEEDS-OWNER.md as
`DECIDED (campaign session 2)`. **If this session is cut off, re-run the same prompt: continue from
the checklist below; do not restart.**

**Plan for this slice (priority order; each fix proven by a failing test first):**
1. [done] Build the owner's one-week trial (N-052, HANDOFF spec): clock per owner account, 402
   `TRIAL_ENDED` on every write/intake path and in the worker, subscribing lifts it, banner and
   full-page notice, two emails, e2e tenants S and T, tenancy doc regenerated.
2. [done] Test corpus under `backend/tests/fixtures/corpus/` (generated, no copyrighted material).
3. [done] P0 sweep of AI safety and isolation: prompt injection through documents (assistant,
   automations, exception agent, assertions), canary isolation across organizations and
   ungranted workspaces (search, assistant, citations, embeddings, caches, suggestions), metering
   exactly once (retries, cancelled streams, fallbacks, BYOK), model list hygiene.
4. [done] Known leftovers: the assistant stream's Redis frame buffer off the event loop (live /health
   proof); the matching queue lists one case per invoice copy (one case, duplicates flagged).
5. [done] Every engine control does something real (AI defaults, presets, legal hold, autonomy
   thresholds, re-index status) and every engine is checked against the new plan matrix.
6. [done] Document life: every intake path, every state, hostile timing (delete/archive/downgrade during
   processing), stuck-job sweeper, idempotent re-runs, provenance.
7. [done] The worker: every handler and sweep by hand and on schedule, two workers never run one job,
   DLQ visible, graceful shutdown, 1,000+ jobs RSS, timeouts, shared models serialized, pool.
8. [done] Automations: action catalog live, loops and budgets; review, collaborative review,
   verifications and assertions agree.
9. [done] COVERAGE.csv rows in the slice to `deep` or `blocked`; finish checks; HANDOFF; PR ready.

**Progress (checkpoint 1, 2026-10-11):** items 1 to 5 done and pushed. Fixed and proven:
F-257 to F-266 (FINDINGS.md, session 2 section), decisions N-053 to N-055. Live proofs with no
defect: retrieval isolation (55 checks), prompt injection, metering exactly once. Engine controls
proven real by new live tests: presets reach the extraction prompt (and stop when disabled), the
autonomy error limit moves the cut-off, the re-index card stops polling, Streaming responses
(F-266), AI defaults on a new organization (F-260); legal hold was already covered
(`tests/api/test_legal_hold_enforcement.py`).

**Progress (checkpoint 2, 2026-10-11):** items 6 to 8 done and pushed. Fixed and proven: F-267
(Reprocess did nothing and left documents Queued; AI never re-ran), F-268 (Reprocess twice ran and
charged twice), F-269 (two workers ran one job; a stopping worker ran its whole batch), F-270 (the
dead-letter queue made visible: /admin/jobs), F-271 (API keys acted with their issuer's full rights
whatever their scopes; N-056). Proven sound with new live tests: deletes mid-pipeline, duplicate
job delivery, every scheduled sweep, 1,200-job memory soak, the rest of the automation action
catalog, API-key uploads. The sandbox restarted once mid-session (databases on the RAM disk were
lost; `frontend/e2e/scripts/start-db.sh` then `start-stack.sh` bring everything back). **Next:**
item 9 (ledger, full suites, PR ready).

**Finished (2026-10-11).** Fixed and proven this session: F-257 to F-274 (18), plus the owner's
trial (N-052). Decisions N-053 to N-056. Finish checks, all on the final code:
- backend: full suite **3,915 passed, 9 skipped, 0 failed** (`POSTGRES_DB=flowpilot_test`);
- browser: full suite on a fresh e2e database, production bundle, CSP enforced, model stand-in on
  (`E2E_LLM=1`, as CI): **409 passed, 1 skipped** (the provider-down test, which only runs without
  a model), 0 failed;
- frontend: `tsc -b`, the browser tests' typecheck, `npm run lint`, `npm run check:self`,
  `npm run build`: all clean (the build's large-chunk notice is pre-existing);
- Alembic: one head (`s2a1_free_trial_clock`), upgrade, downgrade, upgrade on a scratch database;
- audits: `pip-audit -r requirements.txt` and `npm audit --omit=dev`: no known vulnerabilities;
- live logs of the browser run: 0 tracebacks, 0 ERROR lines (API and worker); the one 5xx is the
  expected 503 for a seat purchase with no gateway key in the sandbox; all 468 jobs SUCCEEDED.
Ledger, session 2's slice: 362 `deep`, 9 `blocked`, 83 `smoke`, 18 `untested`; what is left is
listed in HANDOFF.md for session 4. **Next:** the owner merges PR #20; then session 3.

## Previous phase — Pre-launch campaign, session 1 of 4: tenancy, roles, plans, seats, billing, usage limits
Branch `claude/new-session-ejp1sm` (the session's assigned branch), draft PR opened early. The four
sessions run one after another; the hand-over notes between them are in `docs/hardening/HANDOFF.md`.
Owner delegated founder authority: decisions are taken, built and recorded in NEEDS-OWNER.md as
`DECIDED (campaign session 1)`.

**Plan for this slice (priority order; each fix proven by a failing test first):**
1. P0 money/access, found by reading before the stack was up (to be proven):
   - a new organization is put on no plan at all (no Free tier assigned): AI is refused and the
     platform-wide defaults (20x Free) apply instead of Free's allowance;
   - seat changes on paid organizations never reach the payment gateway (the internal outbox
     queue that carries `billing.seat_added/removed` has no consumer), so members are not billed;
   - `organizations.seat_limit` is never written, so every organization, Free included, can add
     unlimited members.
2. One seat model, enforced at every entry path (invitation, direct add, reactivation, SCIM, SSO
   JIT, ownership transfer, partner tenancy): Free capped by the plan; paid organizations hold a
   purchased seat quantity; adding past it buys a seat after the price is shown and confirmed, or
   is refused with the way to buy; Owner and Billing can buy seats.
3. Free made deliberately small: new meters `document.upload` and `assistant.message`, per-plan
   file size, pages per document, workspaces and seats; enforced on every path (browser, multipart,
   API key, batch, public link, re-processing, the assistant) and in the worker; charged at upload
   (reserve) so nothing is left half-processed; one Free organization per account; atomic.
4. The paid ladder: same counts on Developer/Business/Enterprise, a monotonic test over the seed,
   unit economics against the margin service, overage policies doing what they say.
5. `docs/hardening/TENANCY-AND-PLANS.md` generated from the code, with a drift test.
6. Role matrix on the server; removal revokes every kind of access; restricted screens name who
   to ask.
7. Subscription lifecycle through the webhook paths (Stripe test signatures, Dodo), downgrade
   handling (N-003), archive/delete with an active subscription.
8. Live concurrency tests (seats, quota units, webhooks), the worker's tenant gate, revenue truth,
   the admin area refusing tenants, plan cards from the source of truth.

**Session 1 finished (2026-10-11).** All eight plan items done; PR #19 marked ready.
- **Fixed, each with a test that failed first (FINDINGS.md F-236 to F-255):** new organizations
  start on Free; seats enforced on every entry path and purchasable by Owner, Admin and Billing; the
  decided plan table enforced on every upload path, the assistant, workspaces and the worker
  (charged once, at acceptance); one Free allowance per owner account; resumable uploads bounded in
  total; the paid ladder monotonic and every paid seat at least 60% gross margin at its ceilings
  (50% on the dearest platform model); Gemini overage billed and own-key tokens never billed as
  overage; automations from Developer up; a paying organization is never sold a second
  subscription; an organization cannot be deleted while its subscription renews; no automations
  or engine work for an organization that has not paid; allowance shown where it is spent; every
  "ask someone" line names the owners; calendar feeds for owners and admins; an invitation race
  deadlock removed. Proven with no defect: role escalation, Billing-role sweep, add-on lifecycle,
  promo codes (live race).
- **Owner decisions:** N-040 to N-051 taken under delegated authority; **N-052 (the owner's own
  decision): Free becomes a one-week trial, then subscribe. Not built in session 1; the spec and
  the build plan for sessions 2 to 4 are in HANDOFF.md.**
- **Open:** F-256 (P3, late usage into an empty hour not disclosed; the late-usage test fails at
  minute 59), refunds not modelled, plan switching depends on the Stripe portal setting (N-050).
- **Verification:** full backend suite 3,760 passed, 1 failed (F-256's minute-59 case; passes on
  rerun and in sequence), 9 skipped; full browser suite on a fresh e2e database, production bundle with the production CSP enforced: 400 passed, 1 failed, 1 skipped on the final bundle (the one failure was a test-isolation flaw, another test signing C.admin out everywhere; fixed, and both specs repeated three times in parallel pass 45/45). The first full run's 11 failures were two regressions in this session's own F-252/F-253 work, caught and fixed; all 11 pass; `tsc -b`, browser-test typecheck, `npm run
  lint`, `npm run check:self`, `npm run build` clean; pip-audit and `npm audit --omit=dev` no known
  vulnerabilities; one Alembic head (`s1b1_calendar_feed_org_member`), up/down/up on a fresh
  database; no new model drift; live API and worker logs 0 tracebacks, 0 ERROR, the only 5xx the deliberate 503 for buying seats with no gateway configured; 643 background jobs SUCCEEDED, none failed.
- **Next:** session 2 (documents, AI, engines, jobs), starting with the owner's trial (HANDOFF.md).

**Progress (checkpoint 1, 2026-10-11):** items 1 to 5 done and pushed; item 6 partly.
- Fixed, each with a test that failed first (FINDINGS.md F-236 to F-249): new organizations start
  on Free; seats enforced on every entry path and purchasable by Owner and Billing; the decided plan
  table (N-040 to N-047) enforced on every upload path, the assistant, workspaces and the worker
  (charged once, at acceptance, under the organization's lock); one Free allowance per owner
  account; resumable uploads bounded in total (migration s1a1); the paid ladder monotonic and every
  paid seat at least 60% gross margin at its ceilings; allowance shown where it is spent; every
  "ask someone" line names the person; calendar feeds work for owners and admins (migration s1b1);
  an invitation race deadlock removed.
- `TENANCY-AND-PLANS.md` is generated from the code (drift test).
- Verification so far: targeted backend runs 1,076 passed and 580 passed; full browser suite
  397 passed, 3 failed (all one cause, the billing role refused the summary: fixed and tested),
  1 skipped; live logs 0 tracebacks, 0 ERROR, one deliberate 503 (seat purchase with no gateway
  configured, F-213's answer). One Alembic head (s1b1), up/down/up verified, no new model drift.
- Next: E.3 rest (self-escalation, last owner, viewer cannot write), D (overage policies proven,
  add-ons, frontend parity), F (subscription lifecycle via webhooks), G (worker tenant gate,
  webhook concurrency, cache keys, per-tenant rate limits), H (revenue truth, /admin refusal),
  COVERAGE.csv, then the finish checks.


## Latest pass — owner decisions N-034 to N-039 applied (2026-10-10)
The owner delegated founder authority for the six open questions; engineering decided and built
them on the same branch and PR (#18), each proven by a failing test first (FINDINGS.md F-230 to
F-235, NEEDS-OWNER.md for the reasoning):
- **N-037 / F-230:** invitation links last 7 days (`INVITATION_TTL_HOURS` 168).
- **N-036 / F-231:** accepting never lowers an active member's role, only raises it.
- **N-034 / F-232:** a platform-delivered invitation's sign-up takes over an account nobody
  verified (new password, verified, old sessions ended); verified accounts still sign in.
- **N-035 / F-233:** organizations that require SSO are joined through SSO: the preview says so, the
  page offers "Sign in with single sign-on", the password sign-up is refused (409).
- **N-039 / F-235:** the first SSO sign-in takes over an unverified account (verified, password
  unusable, sessions ended, audited).
- **N-038:** torch 2.13.0, setuptools 83.0.0, paramiko 5.0.0 (pip-audit clean on these).
- **F-234:** the "no seats" email to the inviter goes once per invitation per day, not on every
  refused attempt.
Verification: the new tests (11) and the invitation, SSO and SFTP suites (613) pass; the torch and
embedding suites pass apart from the 4 sandbox-only vector tests; browser specs touching
invitations, the team and auth: 143 passed, the new SSO screen test included (one dev-mode-only
miss in 21-live-feedback, which needs the production bundle's hashed chunks and passes there);
`tsc -b`, the browser-test typecheck and lint clean. Full CI runs on the push.

## Earlier pass — invitations, the workspace team and the event loop (2026-10-10)
Same branch and PR as the bug hunt below (`claude/jolly-keller-d0jscd`, PR #18), continued after
a usage-limit pause, on the owner's list (required.txt). **Fourteen defects and builds, F-216 to
F-229, each proven by a failing test first** (FINDINGS.md, "Invitations, the workspace team and
the event loop"), plus the owner's N-032/N-033 decision applied and backend CI made green again.
- **F-218 (High):** the routes that await (uploads, the assistant stream, upload parts, webhooks)
  and the sign-in dependency every request runs did blocking work on the event loop; a slow step
  in any of them stalled the whole API. All of it runs in the threadpool now; a live-server test
  slows each step to 3 s and requires `/health` under 1 s (10 of 10 failed before).
- **Invitations:** F-216 the pending list showed accepted and revoked invitations (the "zombie");
  F-217 removing a member left their pending invitation, whose link let them back in; F-221 the
  last seat could never be filled by an invitation; F-222 a newcomer now signs up from the
  invitation in one step (address locked, account verified by the token) and an existing user is
  sent to sign in and back; F-224 "switch account" now signs out on the server; F-223 the email is
  rebuilt for Outlook, Gmail and Apple Mail (card, bulletproof button, security footer).
- **F-219:** Workspace Settings → Team members has role controls and Remove, or a badge, in every
  Actions cell. **F-220:** an empty or oversized public document-request upload was a 500.
- **N-032 / N-033 decided:** Batch operations and TruthMesh on Business and Enterprise (deploy step:
  `seed_quota_tiers.py --carry-forward`).
- **Review:** an independent adversarial review of the diff found **F-226 (High)**: an
  organization sending mail through its own SMTP could turn an invitation into a verified account
  for any address; now only a link FlowPilot's relay delivered proves an address. Also F-227
  (switch account sent a newcomer to sign-in), F-228 (sign-up dead ends), F-229 (presence order).
- **CI:** backend CI was red on main since the platform relay became mandatory (24 tests); the
  backend steps now declare a relay. `pip-audit (advisory)` stays red on main (N-038).
Verification: see "What is done" below (CI green on the final code).

## Earlier pass — live bug hunt: fuzzing, double clicks, dev-mode rendering (2026-10-09)
Branch `claude/jolly-keller-d0jscd` (the session's assigned branch). Real live stack: Postgres 16 +
pgvector 0.8.0 (built from source), Redis, uvicorn API, the real worker `--loop all --profile all`
(full `requirements.txt`, Paddle included), model stand-in, Vite dev server on :5173. Previous
sweeps had found nothing new, so this pass attacked the stack differently: an OpenAPI-driven fuzzer
over all 609 operations as real users (bad ids, edge values, wrong types, malformed JSON), a
double-click sweep (every write sent 3 to 8 times at once), odd uploads and delete-during-processing
races, and the browser suite against the dev server. **Eight live defects, F-208 to F-215, each
proven by a failing test and fixed** (FINDINGS.md, "Live bug hunt"). The serious ones:
**F-212** two quick clicks on "Change role" froze the whole API until restart (129 `async def`
routes did blocking database work on the event loop); **F-214** concurrent duplicate submits were
500s on 8 routes; **F-215** an OCR worker could not shut down gracefully once Paddle was loaded;
**F-209** re-inviting a pending address crashed; **F-211** an SSO connection made from a metadata
URL was a 500. After the fixes the fuzzer and the double-click sweep return no 500s (the only 5xx
are the intended 503 "billing not configured" answers).
Verification on a fresh e2e database: browser suite against the production bundle with the
production CSP **380 passed, 0 failed, 1 skipped** (by design); against the Vite dev server 379
passed, 1 skipped, and the only failure is the F-144 test, which removes a hashed production chunk
and so only works against the production bundle. API and worker logs over both runs: **0 tracebacks,
0 5xx, 0 ERROR lines; 640 jobs of 38 types, all SUCCEEDED.** Backend suite (full, on the final head): **3,592 passed, 0 failed, 9 skipped** (65 more tests than Phase 3).
Frontend: `tsc -b`, `npm run lint`, `npm run check:self` clean; encoding check clean; one Alembic
head (no migration in this pass). The sweep scripts are kept as `docs/hardening/tools/live_api_fuzz.py`
and `live_double_click_sweep.py`.

## Earlier pass — final systemic polish & traceback sweep
Branch `hardening/final-systemic-polish-and-traceback-sweep` (after PR #15). Real live stack
(API + worker `--loop all` + Postgres/pgvector 0.8 + Redis + production bundle): browser suite 380
passed, 0 failed; 0 tracebacks, 0 5xx, 0 ERROR log lines; 400 jobs all SUCCEEDED. One polish fix,
F-207 (materiality shown as "90%" instead of "0.90"), proven red then green. Stopped early on the
owner's usage budget; the remaining small items below are still open (the corroborator item is done).


## Earlier pass — budget-capped header polish (2026-10-09)
Branch `claude/sweet-hopper-l4varl`. The owner had ~1% of weekly usage for this run, so the live
stack was NOT stood up (the sandbox had no Python dependencies or pgvector build). Only change: ERP
posting and Process intelligence now open with the shared `PageHeader` (icon tile, eyebrow, title,
description underneath, actions on the right) instead of a description squeezed beside the title.
Verified: `tsc -b`, eslint on both files, `npm run build`. Unverified: the browser suite and a live
look (route titles `/ERP posting/` and `/Process intelligence/` in `e2e/support/routes.ts` still
match the same `<h1>` text).

## Earlier phase — Phase 3 (complete)
**Phase 3 — Final commercial hardening: COMPLETE, merged.** Branch
`hardening/phase-3-final-commercial-hardening`; PR #13
(https://github.com/sabarishwarne2001/flowpilot-ai/pull/13). Builds on Phase 2 (merged
into `main`).

- **Live defects (each with a failing test first): F-182 to F-206** (FINDINGS.md, "Phase 3").
  The heavy ones: F-182/F-183 the nightly retention purge never ran, and when run it left the files
  in storage; F-188 requiring SSO with no identity provider locked out the whole organization;
  F-195 a failed request re-asked ~30 times a second (976 failed requests in 15 s from one tab);
  F-196 every new workspace was set to a Groq model Groq had retired; F-204 an organization's
  maximum session age was shown as in force but never applied; F-205 the public SAML logout
  endpoint signed people out on an unsigned request. Also role-matrix truthfulness (F-192 to
  F-201), the job SLOs never measured (F-189), BYOK and margin miscounts (F-190, F-202), RevOps
  prompts that sent on Cancel (F-186), billing in internal keys (F-187), a phone-width overflow
  (F-203), and a deliberate sign-out that sent people back where they were (F-206).
- **Elevation:** one console header (`PageHeader`) and URL-synced keyboard tabs across every
  organization console, the identity and billing hubs, marketplace, autonomy, audit and the platform
  admin consoles; sentence case and labelled fields on the remaining settings and auth screens.

## What is done
- **Invitations, the workspace team and the event loop (2026-10-10)** (PR #18): F-216 to F-229
  fixed, each proven by a failing test first.
  - **GitHub CI on the final code (`bfe1836`): green**: the full pytest job (Phase 2 security
    proofs, Phase 4 engine proofs, the whole suite), browser tests, frontend, migration head and
    drift. Only `pip-audit (advisory)` is red, as on `main` (N-038).
  - Browser suite on a fresh e2e database, production bundle with the production CSP: **393
    passed, 0 failed, 1 skipped** (by design; 13.2 min). API and worker logs: 0 tracebacks, 0 5xx,
    0 ERROR lines; 410 jobs of 38 types, all SUCCEEDED.
  - Backend, full suite locally in CI's environment: **3,631 passed, 9 skipped, 8 failed**. The 8
    are sandbox-only and pass in CI (the embedding model cannot be downloaded here, plus one
    readiness check).
  - `tsc -b`, the browser-test typecheck, `npm run lint`, `npm run check:self`, `npm run build`
    (no source maps) and the encoding check are clean. One Alembic head
    (`r1a1_invitation_delivery`); up, down and up again; no new drift (283 known).
- **Phases 0 to 5, final release, production configuration & UI elevation, final systemic
  polish, live feedback & Tier-1 elevation, Phase 1 (document intelligence), Phase 2 (enterprise
  processing and TruthMesh):** merged.
- **Phase 3 — final commercial hardening** (this branch): F-182 to F-206 fixed, each proven.
  - Backend suite (full): **3,527 passed, 0 failed, 9 skipped** (48 min; 48 more tests than Phase 2).
  - Browser suite (full, fresh database, production preview, CSP, model stand-in): **380 passed, 0 failed, 1 skipped** (by design; 12.3 min). The first full run had 2
    failures, both fixed and re-proven: the API-keys header added here matched a test's phrase
    (`a29d764`) and F-206. Logs clean; 403 jobs of 38 types, all SUCCEEDED.
  - `npm run build`, both `tsc` projects, lint, `npm run check:self`, `check-no-sourcemaps
    --dist`, encoding check, `npm audit --omit=dev` (0): clean. One Alembic head
    (`q1a1_retired_groq_models`); migration up, down and up again checked; drift 283 known, 0 new.
  - `COVERAGE.csv` (1,400 rows): deep 380 → **408**.
  - Release certification: `05-release-readiness.md` (Phase 3), verdict **GO**.

## Owner decisions in force (do not re-ask)
**N-032 and N-033 decided 2026-10-10:** Batch operations and TruthMesh are on Business and
Enterprise. **N-034 to N-039 decided 2026-10-10** under delegated founder authority: unverified
accounts are taken over by a platform-delivered invitation or an SSO sign-in, SSO-required
organizations are joined through SSO, accepting never lowers a role, invitations last 7 days, the
flagged dependencies are upgraded.
Everything else in NEEDS-OWNER.md is decided. Previous release: **N-026** Stripe (test mode) for
launch, Dodo selectable; **N-027** Postmark before the first paying customer; **N-028**
`app.flowpilot.ai` / `admin@flowpilot.ai`; **N-029** no unbacked trust claims; **N-030** seat price =
plan card price; **N-031** local model at a declared zero.

## Next action (exact)
_Updated 2026-10-17 (website and payments session close):_ once CI is green on pull request #25,
merge it. Then the owner works through HANDOFF.md "Owner launch checklist" (12 steps; step 9 is the
first run against a real Dodo test account, checking docs/billing/DODO.md §6). The next session
removes the one-release aliases for the old name and takes up anything step 9 finds.

_Updated 2026-10-14 (session 4 close):_ merge pull request #23 once CI is green, then start a new
session with `docs/hardening/NEXT-SESSION-PROMPT.md`; the owner works through HANDOFF.md
"Owner launch checklist". The list below is the earlier (session 3) text, kept for history.

_Updated 2026-10-12. The earlier list here (merge PR #18; the Redis frame buffer; one matching case
per invoice copy) is done: PR #18 merged, the buffer moved off the event loop (F-263), one case per
invoice (F-264, N-055)._
1. Session 3 is done (its PR is "Campaign session 3: consoles, identity, settings and security").
   Next: session 4 (experience, performance, operations, certification), with its section of
   `docs/hardening/CAMPAIGN-MAP.md`, starting from HANDOFF.md "Session 3 → later sessions"
   (F-324 to verify first).
2. Still open from earlier passes, assigned to session 4 in the map: the main JavaScript chunk's
   size, measured fresh, and the Tailwind 4 move.
3. The owner's own launch steps (Stripe test prices and webhook secret F-125, Postmark N-027, rolling
   the keys pasted into chats, the first deploy RUNBOOK §9, and the rest) are collected in section 5
   of the campaign map; session 4 turns them into the final "Owner launch checklist".

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh` (pgvector
0.8.0 built from source: `apt-get update && apt-get install postgresql-server-dev-16`, then
`make && make install` in a v0.8.0 checkout), `backend/.env` from `.env.example` plus the block in
`frontend/e2e/README.md` (and `POSTGRES_PORT=5433`, `POSTGRES_DB=flowpilot_e2e`, the CI secrets from
`.github/workflows/ci.yml`), `python3.12 -m venv backend/.venv && pip install -r requirements.txt
-r requirements-dev.txt`, `npm ci` in `frontend`, then `E2E_LLM=1 frontend/e2e/scripts/start-stack.sh`
and `E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e`. Backend tests: the CI environment block
(`POSTGRES_*` pointing at port 5434 — a second cluster — `ARCH40_CONTRACT=1`, the CI secrets), then
`pytest -q`. For a fresh browser run also flush the e2e Redis database (`redis-cli -n 0 flushdb`)
and delete `frontend/e2e/.state/`.

## Blockers
None in engineering. Not verifiable from this environment: the production compose stack on a real
server (F-006) and the CPU-only torch image (F-045).

## Budget notes
One long session (continued after its context filled): the live stack with log watch across every
console, hub, admin page, role and plan; a crawler pass per seeded person and at phone and tablet
widths; every scheduled sweep by hand; 25 fixes each proven by a failing test; the console
elevation; one full backend run and one full browser run on a fresh database; the documentation.
The exact spend is not visible from inside the session; check your usage page.

## Environment notes (for the next session)
- **Event-loop freezes**: `tests/engines/test_blocking_io_does_not_freeze_the_api_live.py` is the
  pattern: slow one blocking call to 3 s inside a route on a real uvicorn and time `/health`. The
  guard `tests/core/test_async_routes_must_await.py` now also checks async dependencies and the
  app coroutines a route awaits.
- **Context variables and the threadpool**: a value set with `set_current_principal` inside a
  `run_in_threadpool` call is lost when the thread returns; set it on the loop (see
  `deps.get_current_user`).
- **The worker with `--profile all` needs the full `requirements.txt`** (Paddle, torch): with only
  `requirements-web.txt` it refuses to start (ProfileError), by design. `pip install -r
  requirements.txt` takes ~10 minutes in the sandbox.
- **A frozen API**: `pip install py-spy` then `py-spy dump --pid <uvicorn pid>` shows what the event
  loop thread is blocked on, and `pg_stat_activity` / `pg_blocking_pids()` shows who holds the lock.
  This is how F-212 was traced.
- Concurrency defects only show under a real server: `tests/engines/test_concurrent_*_live.py`
  start uvicorn in a thread (TestClient serialises requests differently).
- **Two pytest processes against one Postgres server corrupt each other** (fixed database names,
  e.g. `flowpilot_svc_test`). Use the second cluster (`POSTGRES_PORT=5434`) and, for a targeted run
  next to a full one, `TEST_DB_NAME=<other>`; the services suite still shares its fixed name.
- **Do not edit backend source while a full suite runs**: several security tests read source with
  `inspect`/`ast` and see the edited file at the old line numbers.
- After changing `backend/requirements.txt`, run `python scripts/image_requirements.py`.
- FastAPI is pinned below 0.137 on purpose (route walkers; see Phase 5 notes).
- The browser suite needs a restart of the stack after backend changes (`start-stack.sh`; uvicorn
  runs without reload). `E2E_LLM=1` starts the model stand-in; `E2E_CSP=1` enforces the Caddyfile CSP.
- `pkill -f <pattern>` can kill the calling shell; use anchored `pgrep -f '^...'` and kill by PID.
- Model hosts, Stripe, Groq and Dodo are blocked; PyPI and npm are reachable. PyPI throttles
  repeated installs: resolve with `pip install --dry-run --report`, fetch the wheels once, then
  `pip install --no-index --find-links`.
- In dev mode (`E2E_MODE=dev`, Vite on :5173) set `E2E_API_ORIGIN=http://localhost:8000`, or the
  refresh cookie's domain does not match and every page signs out.
- `frontend/e2e/probe/` (untracked, excluded in `.git/info/exclude`) holds screenshot probes:
  `E2E_TEST_DIR=./probe npx playwright test -c e2e p8` writes PNGs to `/tmp/shots`.
- **Live sweeps**: the crawler and API sweep scripts used in this pass are described in
  FINDINGS.md ("Final systemic polish"); the jobs table (`select job_type, status, count(*) from
  jobs group by 1,2`) is the record of every background job the live stack ran, and survives the
  log rotation that `start-stack.sh` does on every restart.
- The browser suite reuses a preview server on port 3000 if one is running (outside CI), so a run
  started while another is serving tests the older bundle.
- **Fresh e2e database**: `psql -h localhost -p 5433 -U postgres -c "DROP DATABASE flowpilot_e2e WITH (FORCE)"`
  then `CREATE DATABASE flowpilot_e2e` and `start-stack.sh` (migrates). Some browser tests create
  organizations; archived ones count towards an account's limit of three, so a database that has
  seen many runs eventually refuses new ones.
- The browser-suite seed now finds its sample documents by name (F-168); before that, a database
  with more than 50 documents in Tenant C received a second copy of each sample on every run.
- Ad-hoc screenshot probes (not part of the suite) can be written under `frontend/e2e/tools/`
  and run with `E2E_TEST_DIR=./tools`; this phase's probes were kept out of the repository.
- `seed_price_book.py --version auto` publishes the next price book only when the seed changed;
  the start scripts and the e2e seed use it.
- **TanStack Query**: the client refetches on mount ("always"); `retryOnMount: false` (F-195) stops a
  failed query being re-asked whenever another component reading it mounts. Do not gate a page on
  `isLoading` while a child reads the same query without it.
- **Groq models**: Groq retired `mixtral-8x7b-32768` (March 2025), `llama-3.3-70b-versatile` and
  `llama-3.1-8b-instant` (16 August 2026). The platform default is `openai/gpt-oss-20b`; check
  https://console.groq.com/docs/deprecations before adding a model to `app/core/ai_models.py`.
- Sessions live in the `sessions` table; a refresh that fails revokes the family, so a script that
  ages a session and refreshes loses its access token too.
- **The sandbox's databases live in memory** (`/dev/shm/pgdata` :5433 for e2e, `/dev/shm/pgdata2`
  :5434 for backend tests): a container restart wipes them. Recreate both clusters with
  `initdb --encoding=UTF8 --locale=C.UTF-8` (the container's POSIX locale otherwise gives
  SQL_ASCII and the tests fail on the first non-ASCII character), the `vector` extension, the
  `flowpilot` role and the `flowpilot_test` / `flowpilot_e2e` databases, and start Redis; then
  `start-stack.sh` migrates. On a fresh e2e database the very first browser run can fail with
  `UnmappableTierError` (the seed publishes the plans at that instant); the next run passes.
