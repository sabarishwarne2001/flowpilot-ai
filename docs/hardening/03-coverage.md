# Phase 3 — Browser tests and full-product coverage

_Branch `hardening/e2e`. Written 2026-10-05 at the Phase 3 checkpoint._

## 1. In plain language

FlowPilot now has a robot that uses the product the way a person does: it opens a real
(headless) Chrome, signs in as a customer, uploads documents, clicks through every page and
module, fills in forms, reads the emails the product sends, and checks what happens. It is
deliberately strict: a page that prints an error in the browser console, throws, or gets an
unexpected error from the server **fails** the test, even if the screen looks fine.

It found one crash that took the whole server down (fixed in this phase), a bug that stops
paying customers from adding team members, a broken audit-log export, and a set of smaller
permission, error-message and "missing feature" problems. They are listed in section 5 and in
`FINDINGS.md` (F-049 to F-066).

**Final full run (one run, 275 tests, 10.4 minutes, 2 parallel browsers):
233 passed, 40 failed, 2 skipped.** Every one of the 40 failures is a product finding, not a test
problem (each is listed below with its finding id); the 2 skips are the assistant-answer tests,
which need an LLM key this sandbox does not have.

## 2. What was built

| Piece | Where | What it does |
|---|---|---|
| Playwright 1.56.1 config | `frontend/e2e/playwright.config.ts` | headless Chromium; builds the **production** bundle and serves it with `vite preview`, `/api` proxied to the local API (same origin, as behind Caddy). `E2E_MODE=dev` tests the dev server instead. |
| Strict fixture | `frontend/e2e/support/fixtures.ts` | fails a test on uncaught errors, unhandled promise rejections, `console.error`, any HTTP ≥ 400 and failed requests, unless the test declares that exact error as expected (with a reason). Captures the response body of every failed call as evidence. |
| Fresh session per test | same | refresh tokens rotate and a reused one ends the session family, so saved sessions cannot be shared; each test signs in through the API and the app does its normal cookie refresh. |
| Seed | `backend/scripts/seed_e2e.py` | deterministic and idempotent; runs the production `seed_price_book.py` and `seed_quota_tiers.py` with test-mode price ids; resets roles, passwords, subscriptions and custom SMTP every run. |
| Sample documents | `frontend/e2e/support/sample-docs.ts` | two invoices (one with changed bank details), a purchase order, a goods receipt, a contract with obligations, a 4-page packet, plus the repo's PNG logo and one corpus PDF; uploaded through the real upload API and processed by the real worker. |
| Mail catcher | `frontend/e2e/support/smtp-sink.mjs` + `support/mail.ts` | a 100-line local SMTP server; tests read verification, reset and invitation links from it. |
| Stack scripts | `frontend/e2e/scripts/start-db.sh`, `start-stack.sh` | native Postgres + Redis (no Docker here); API + worker + mail catcher with PID files and logs. |
| Known-issue registry | `KNOWN_ISSUES` in the fixture, `tests/known-issues.spec.ts` | F-049 (avatar 404 on every page) would fail all 275 tests; it is reported on each test instead, and a proof test turns red once it is fixed. |
| CI | `.github/workflows/ci.yml`, job `e2e` | on pull requests, **advisory** (not in the required `CI` gate until Phase 4 turns it green); uploads the report, screenshots, traces and server logs. The Frontend job now type-checks `frontend/e2e`. |
| Inventory tools | `frontend/e2e/tools/` | page and form accessibility-tree dumps used to write the tests; not part of the suite. |

Seeded accounts (development/test only; password in `support/env.ts`):

| Tenant | Plan | Users (organization role / workspace role) |
|---|---|---|
| A — DevCo Labs | Developer | owner (OWNER/ADMIN), admin (ADMIN/ADMIN), billing (BILLING/VIEWER), member (MEMBER/CONTRIBUTOR), viewer (MEMBER/VIEWER) |
| B — BizCo Holdings | Business | owner |
| C — Caretakers Global Inc | Enterprise | owner, admin, member, viewer; workspaces Operations and Finance |
| P — FlowPilot Platform Ops | Free | platform super-admin |

Tenant C has a member and a viewer in addition to the brief, so role checks on Enterprise-only
features (ERP posting) test the role, not the plan.

## 3. Environment and its limits (read before trusting a green test)

- **No Docker daemon** in this sandbox: Postgres 16 + pgvector 0.8.0 (built from source) and Redis
  run natively on a RAM disk; storage is `STORAGE_BACKEND=local`, not MinIO.
- **`ML_STUBS=true`**: digital PDFs keep their real text layer; images get labelled stub OCR;
  embeddings are word-hash stubs. So entity records, tables, three-way match cases, radar flags and
  low-confidence review items were **not produced** and their results could not be tested (F-063).
- **No LLM key** (Groq/Gemini unreachable): the assistant's answer tests are **skipped with that
  reason** (`E2E_LLM=1` turns them on); the "AI unavailable" behaviour was tested instead (F-056).
- **No Stripe test key, no outbound DNS/HTTP**: checkout, the billing portal, webhook delivery,
  warehouse sync, BYOK key validation and custom-domain DNS checks can only show their error
  handling here.
- **Rate limiting off** for the test stack (`RATE_LIMIT_ENABLED=false`): every test signs in, and the
  real limit is 10 per 5 minutes per IP (F-048). Phase 2 proved the limiter itself.
- One container restart mid-phase wiped the RAM-disk database; `start-db.sh` + the seed rebuilt it
  in two commands, which also proved the seed on an empty database.

## 4. Results

| Spec file | Covers | Passed | Failed | Skipped | Failures are |
|---|---|---|---|---|---|
| `00-harness` | API health, seed state, the strict listeners catch all four problem kinds | 2 | 0 | 0 | — |
| `01-page-smoke` | all 21 workspace pages, 18 organization pages, 3 platform pages, 6 public pages, redirects, 404 | 52 | 1 | 0 | F-058 (Branding logo 404) |
| `02-plan-matrix` | 20 gated items × Developer / Business / Enterprise: sidebar lock, upgrade dialog → Billing, lock card by URL, 402 from the API, zero locks on Enterprise | 63 | 1 | 0 | F-058 |
| `03-role-matrix` | sidebar per role, forbidden pages by URL, viewer pages, platform pages vs tenant admins, server refusal of org edits, API keys, ERP posts | 42 | 18 | 0 | F-054 ×12, F-053 ×3, F-015, F-055, F-065 |
| `10-workspace-documents` | overview KPIs, quick nav, notifications + centre, single / PNG / batch upload to COMPLETED, bad file type, search / filter / sort, viewer tabs, delete | 11 | 3 | 0 | F-061 ×3 |
| `11-assistant-intelligence` | assistant (AI down; answers skipped), extraction memory mode, entities, cases, packet split review, tables, obligations (create, status, calendar, .ics/.csv) | 10 | 3 | 2 | F-056, F-063 ×2 |
| `12-processing` | matching queue and tolerance publish, ERP target, process sweep and tabs, radar filters, corroborator | 8 | 3 | 0 | F-057 ×2, F-063 |
| `13-automation-review` | rule builder (validation, save, active), the rule firing on upload into Run history, review queue filters / take / discuss / bulk, clause checks | 8 | 1 | 0 | F-063 |
| `14-configuration` | profile, workspace rename, settings sections, unsaved guard, redaction studio, Ctrl+K, workspace switcher + isolation | 5 | 3 | 0 | F-058, F-062, F-061 |
| `20-organization` | every organization page with real input (see section 5) | 23 | 7 | 0 | F-051, F-052, F-058 ×3, F-061 ×2 |
| `30-auth` | sign-up + email verification + first organization, duplicate sign-up, form login, wrong password, sign-out, password reset by email, session revoked elsewhere | 8 | 0 | 0 | — |
| `known-issues` | F-049 still reproduces | 1 | 0 | 0 | — |
| **Total** | | **233** | **40** | **2** | |

Plan matrix in one line: on **Developer** all 12 Business/Enterprise workspace items and the 4
Business/Enterprise organization items are locked in the sidebar, open the upgrade dialog, lead to
Billing, show a lock card by URL and are refused by the API with **402**; on **Business** the
Business items work and the 6 Enterprise items are locked; on **Enterprise** nothing is locked and
every page renders. Role matrix in one line: the server refused every mutation it should (organization
edits by Billing/Member/Viewer, API-key create/revoke by non-admins, ERP posting by Viewer) except
ERP posting by a Member (F-065); the UI side has the gaps in F-053, F-054, F-055 and F-015.

## 5. What the tests found

| ID | Sev | What | Status |
|---|---|---|---|
| F-050 | P0 | Opening a scanned packet's review screen aborted the whole API (PDFium used by several threads at once) | **fixed** in `834f99e`, regression test |
| F-051 | P1 | Accepting a team invitation → HTTP 500 on every organization with a live subscription (`billing.seat_added` rejected by a database constraint) | open |
| F-052 | P2 | Audit log export always fails (`format=CSV` vs `csv`) | open |
| F-053 | P2 | Viewers see Workflows, Run history, Review queue but every one fails with 403 | open, N-019 |
| F-056 | P2 | Assistant loses the user's question when the AI is unavailable | open |
| F-063 | P2 | Results of several AI features cannot be proven with stub models | blocked |
| F-065 | P2 | Members (contributors) may create ERP postings | open, N-019 |
| F-066 | P2 | Seven more PDFium call sites without the lock | unverified |
| F-049 | P3 | Avatar 404 on every page for users without one | known issue |
| F-054 | P3 | Admin pages opened by a Member say "couldn't be loaded" instead of "no permission" | open |
| F-055 | P3 | BILLING role gets a 403 on every organization page | open |
| F-057 | P3 | Forms show "status code 422" instead of the server's explanation | open |
| F-058 | P3 | "Not configured" reported as 404 errors on healthy pages | open |
| F-059 | P3 | Locked page opened by URL has no upgrade button | open |
| F-060 | P3 | "Claim domain" offered where custom domains are switched off (501) | open |
| F-061 | P3 | Capabilities in the brief that do not exist (document search, notification filters, promo code, webhook ping, viewer page image, viewer field correction) | N-020 |
| F-062 | P3 | Unsaved-changes guard misses the workspace General form | open |
| F-064 | P3 | `RATE_LIMIT_*` settings are never read | open |

Already-known findings these tests re-confirmed: **F-015** (the sidebar hides API keys from
ADMIN although N-006 allows them), **F-048** (sign-in limit).

Things the tests proved **work** (selection): 41 of the 42 pages render for the Enterprise owner
without a single console error apart from F-049 (Branding logs F-058); the plan lock matrix is
correct in the sidebar, on the page and on the server (402) for all three paid tiers; platform
pages refuse tenant owners and admins (the UI redirects; the API answers 404 so it does not reveal
itself) and serve the super-admin; sign-up → email verification → sign-in → first organization;
wrong password; sign-out; password reset by email (old password refused, new accepted); session
ended elsewhere → back to sign-in; single, PNG and batch upload through real processing to
COMPLETED; .txt refused with a message; search, filters, sort and delete on Documents; the
workflow builder's validation, saving an active rule, and that rule firing on upload with the run
in Run history; scanned-packet split review; obligations created, opened, exported (.ics/.csv);
tolerance policy publish; ERP target creation; process-intelligence sweep; the redaction studio;
Ctrl+K navigation; workspace switching with tenant isolation; API keys (secret shown once,
revoke); webhooks (signing secret shown once); gateway keys; a custom SMTP server whose test email
arrived; a BYOK provider key; an S3 warehouse destination; an identity domain claim with its TXT
instructions and every SSO/SCIM/security section; 90-day retention; DPA export bundle;
erasure impact preview; egress allow-rule, destination test and lockdown on/off; SLO dashboard;
plan cards at $49 / $299 / $799 with Enterprise marked current; spend limit; the seat field (changing
it needs checkout, which has no Stripe key here).

## 6. The ledger (`COVERAGE.csv`)

Before Phase 3, every `page`, `nav_item` and `ui_action` row was `untested`. A row moved only on a
**passing** test (`docs/hardening/tools/e2e_to_ledger.py` maps test titles to rows; `deep` only for
journeys that change state and check the result). A row whose tests fail keeps its status, gets
`last_result = fail (e2e …)` and the finding id.

| Type | deep | smoke | untested | total |
|---|---|---|---|---|
| page | 7 | 56 | 19 | 82 |
| nav_item | 4 | 36 | 9 | 49 |
| ui_action | 18 | 10 | 27 | 55 |
| endpoint | 6 | 1 | 552 | 559 |
| background_job | 0 | 0 | 73 | 73 |
| config_var | 20 | 33 | 255 | 308 |
| integration | 0 | 0 | 32 | 32 |
| migration | 0 | 151 | 0 | 151 |
| **all** | **55** | **287** | **967** | **1309** |

Phase 3 moved **147 rows** (129 on passing tests, 18 recorded as failing). Before it: 26 `deep`,
185 `smoke` (all from Phases 1–2, mostly migrations and production settings), 1,098 `untested`.

**Rows whose browser tests fail (18):** `/invitations/accept` (F-051), `/organizations/:orgSlug/branding` (F-058), `org:branding` (F-058), `team.invite` (F-051), `team.accept` (F-051), `team.role-change` (F-051), `team.remove` (F-051), `docs.correct-field` (F-063;F-061), `review.approve` (F-063), `review.reject` (F-063), `review.escalate` (F-063), `webhooks.test` (F-061), `audit.browse-export` (F-052), `settings.ai` (F-058), `settings.document` (F-058), `settings.email` (F-058), `settings.unsaved-guard` (F-062), `branding.domain` (F-058).

**Untested pages (19):** `/confirm-email-change`, `/auth/sso/complete`, `/request/:token`, `/organizations/new`, `/no-access`, `/organizations/:orgSlug/workspaces/new`, `/organizations/:orgSlug/branding`, `/organizations/:orgSlug/billing/return`, `/admin`, `/partners`, `/profile/*`, `/account/*`, `/:orgSlug/:workspaceSlug/assistant/c/:conversationId`, `/:orgSlug/:workspaceSlug/entities/:entityId`, `/:orgSlug/:workspaceSlug/tables/:tableId`, `/:orgSlug/:workspaceSlug/corroboration/:runId`, `/:orgSlug/:workspaceSlug/erp/postings/:postingId`, `/:orgSlug/:workspaceSlug/process/proposals/:proposalId`, `/:orgSlug/:workspaceSlug/procurement/:caseId`.

**Untested nav items (9):** `palette:create-workspace`, `org:service-levels`, `org:compliance`, `org:byok`, `org:analytics`, `org:marketplace`, `partner:portal`, `global:theme-toggle`, `global:mobile-drawer` (their pages are smoke-tested by URL; the sidebar click itself is not).

**Untested UI actions (27):** `auth.sso-login`, `auth.change-password`, `auth.email-change`, `auth.step-up`, `org.archive`, `ws.create`, `team.invite`, `team.accept`, `team.role-change`, `team.remove`, `team.ownership-transfer`, `docs.correct-field`, `review.approve`, `review.reject`, `review.escalate`, `assistant.ask`, `assistant.stream`, `billing.portal`, `billing.cancel`, `billing.addon`, `webhooks.test`, `audit.browse-export`, `settings.ai`, `settings.document`, `settings.email`, `settings.unsaved-guard`, `branding.domain`.
Several of these have a test that **fails** on a finding (above) rather than no test at all.

**Not touched by Phase 3:** the 552 untested `endpoint` rows, 73 `background_job` rows, 32
`integration` rows and the untested `config_var` rows (section 7).


## 7. What was not done in Phase 3 (honest list)

- **Endpoint coverage from the OpenAPI schema (brief item e)** and **background-job tests (item f)**
  were not built: the browser suite exercises endpoints through the UI, but the 552 untested
  `endpoint` rows and 73 `background_job` rows are still `untested`. This is the largest remaining
  coverage gap; it needs its own budget.
- The **full backend pytest suite was not re-run** after the F-050 fix (about 25 minutes on this
  machine). The fix's own regression test, the ARCH-43 gate (12/12) and the server-side plan-gating
  suite pass. Evidence Rule step 4 for F-050 is therefore **unverified**; run `pytest -q` before merge.
- Not covered by any test yet: SSO/SAML sign-in, SCIM provisioning, email change, ownership
  transfer, organization archive, workspace creation, `/request/:token` public upload, the partner
  portal, `/no-access`, the billing return page, the theme toggle, assistant answers (no LLM),
  checkout and cancellation (no Stripe key), quota-reached behaviour.
- Mobile layout, accessibility audits and visual regression were out of scope.

## 8. Next actions (Phase 4, in order)

1. **F-051** (P1): migration adding the seat events to the outbox vocabulary + regression test;
   check the JIT and deprovision paths that emit the same event.
2. **F-052, F-053, F-056, F-065** (P2) after your answers to **N-019**; **F-066** stress tests.
3. Run the full backend suite against this branch (F-050 Evidence Rule step 4).
4. The P3 list (F-049, F-054, F-055, F-057 to F-060, F-062, F-064), then remove F-049 from
   `KNOWN_ISSUES` and make the `e2e` CI job required once it is green.
5. **N-014**: the FastAPI/Starlette upgrade, now with this suite as an extra safety net.
6. Endpoint and background-job coverage (section 7).
