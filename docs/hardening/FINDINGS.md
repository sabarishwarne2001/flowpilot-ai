# Findings

How to read this file:

- **Severity.** P0 = stop-ship (data leak, money loss, cannot run). P1 = must fix
  before a paid launch. P2 = must fix before enterprise sales or a real security
  review. P3 = quality or hygiene.
- **Status.** `unverified` = seen by reading code only; no test has proved it yet.
  `confirmed` = a failing test or a live run proved it. `fixed` = the Evidence
  Rule in CLAUDE.md is met (failing test, fix, that test passes, suite passes).
- Every Phase 0 finding is `unverified`: Phase 0 does not run code. Severities
  are provisional until a test proves the behaviour.

| ID | Sev | Status | Area | Title |
|----|-----|--------|------|-------|
| F-001 | P1 | **fixed** (Phase 1, PR #2) | CI | CI on `main` fails instantly with zero jobs; the encoding gate would also fail (44 files) |
| F-002 | P1 | confirmed (static count) | Tests | ARCH-31..50 features, billing webhooks and the frontend have no automated tests |
| F-003 | P2 | unverified | Config/secrets | Hard-coded default secrets are used if the env var is missing |
| F-004 | P2 | unverified | Plan gating | Some endpoints behind a locked nav item have no server-side plan check |
| F-005 | P3 | unverified | Plan gating / UX | Sidebar lock state does not match what the server enforces |
| F-006 | P2 | unverified | Jobs / ops | Retention, backup and 13 sweepers run only from host cron, which the prod compose file does not start |
| F-007 | P3 | confirmed (git) | Repo hygiene | Binary in history, empty README/LICENSE, UTF-16 requirements, ~250 historical scripts |
| F-008 | P3 | unverified | Frontend guards | Route-level role guard exists but is unused; org pages have no route-level role check |
| F-009 | P3 | unverified | Frontend | `/admin` has no index route (likely an empty screen) |
| F-010 | P3 | confirmed (static) | Config | `frontend/.env.example` omits `VITE_API_URL`, the only variable the code reads |
| F-011 | P3 | unverified | Config | 246 of 302 backend settings are missing from `.env.production.template` |
| F-012 | P3 | unverified | Auth | Live-review WebSocket accepts the access token in the URL query string |
| F-013 | P3 | unverified | Billing | Three webhook routes can receive Stripe events; each must be proven to verify signatures |
| F-014 | P3 | unverified | Tenancy URLs | Organization slug `request` is not reserved and collides with the public `/request/:token` page |
| F-015 | P3 | unverified | UI/API roles | Sidebar hides some pages from ADMIN that the API allows ADMIN to use |
| F-016 | P1 | confirmed (full pytest run) | Tests | The backend test suite is red: 2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546 |
| F-017 | P2 | confirmed (autogenerate) | Schema | 314 model/migration drift operations; the CI drift gate could never fail |
| F-018 | P1 | **fixed** (Phase 1, PR #2) | Startup / CI | A fresh clone could not install, migrate, test or start (13 blockers) |
| F-019 | P1 | unverified (code read; unambiguous) | OCR | On one engine error class, OCR silently returns invented text |
| F-020 | P1 | confirmed (live upload + ORM check) | Ingestion | Any chunk without a bounding box makes the whole document fail |
| F-021 | P2 | **fixed** (Phase 2) | Secrets | A rejected BYOK API key is echoed back in the 422 response |
| F-022 | P2 | unverified (gate 0V-G13) | Tenancy | An AI agent tool selector takes no tenant scope |
| F-023 | P2 | unverified (gate arch08_step6) | Security | Five modules read `X-Forwarded-For` themselves (spoofable client IP) |
| F-024 | P2 | confirmed (same command fails live) | Deployment | Production `migrate` fails on a fresh database (ARCH-40 contract flag) |
| F-025 | P3 | confirmed (gate arch20 + drift) | Models | `organization_addon` model is not registered in `app/models/__init__` |
| F-026 | P3 | confirmed (live) | Uploads | Storage errors surface as a raw 500 on upload |
| F-027 | P3 | confirmed (live) | Dev env | Dev compose makes the shell's `AWS_ACCESS_KEY_ID` the MinIO root user |
| F-028 | P3 | confirmed (Playwright) | Frontend | Dashboard fires 4 failing avatar requests for users without an avatar |
| F-029 | P3 | confirmed (gate run) | Gates | 33 of 78 verification gates fail; several are stale |
| F-030 | P3 | confirmed (test runs) | Tests | The test harness is slow and order-dependent |

---

## F-001 — CI on `main` fails instantly with zero jobs (P1, fixed in Phase 1)

**Fixed.** Root cause: `ci.yml:297` used a double-quoted string inside a
`${{ }}` expression (`join(needs.*.result, ",")`). GitHub accepts only
single-quoted strings there and rejected the whole file. actionlint reported
the lex error before the fix and nothing after it, and run 98 on PR #2 was
the first run in the repository's history to create jobs. The 44 encodings
were normalised in their own commit. Encoding and Frontend then passed on
GitHub.

**Plain language.** The automatic checks that should run on every push to `main`
are not running at all. Every one of the last 5 runs ended as "failure" in the
same second it started, and GitHub created **no jobs** for any of them. So for
now nothing automatically stops a broken change from landing on `main`.

**Evidence.** GitHub Actions runs 36674718556, 36673514318, 36671244692,
36586037060, 36582994316 (all `conclusion: failure`, `jobs: []`). The run
title shows the file path `.github/workflows/ci.yml` instead of the workflow's
name `CI`, which is what GitHub does when it rejects the workflow file before
starting it. The YAML itself parses locally. Likely causes (unverified):
a workflow feature GitHub rejects, or Actions being disabled or out of minutes
for the account.

**Second layer (confirmed locally).** Even once GitHub starts the jobs, the
first job, `encoding`, which every other job waits on, would fail.
`python3 backend/scripts/normalize_encodings.py --check` exits 1 on this
commit: `backend/requirements.txt` is UTF-16, and 43 files start with a UTF-8
BOM (e.g. `frontend/src/services/api/client.ts`, `frontend/src/types/tenancy.ts`).
The script offers `--apply`. That is a mechanical fix for Phase 1, one commit.

**Next step.** Phase 1: open the run page on GitHub for the error text, fix
the workflow, fix the encodings, and prove a green run on the baseline branch.

## F-002 — Large parts of the product have no automated tests (P1, confirmed by static count)

**Plain language.** There are 131 backend test files, but none of them calls
the APIs of the newest ~20 features. Those include every locked
"premium" feature, the billing webhooks and the Dodo gateway. The frontend has
no test files at all. The `verify_arch*.py` scripts that "certified" these
features read the source code. They do not run the product, so they cannot
show that a feature works.

**Evidence.** `grep -rl` over `backend/tests` for each API path returns 0
files for: `/procurement`, `/redactions`, `/assertions`, `/anomalies`,
`/autonomy`, `/extraction-memory`, `/entities`, `/packet-splits`, `/cases`,
`/tables`, `/corroboration`, `/obligations`, `/erp/`, `/review`, `/process`,
`/egress`, `/admin/sovereign`, `/admin/revops`, `/billing/webhooks`, and
`dodo_gateway`. `find frontend/src -name "*.test.ts*"` returns 0 files.
The ledger tags the affected endpoint rows with `F-002`.

**Next step.** Phase 3 builds the harness. Until then these rows stay `untested`.

## F-003 — Default secrets are hard-coded in the app config (P2, unverified)

**Plain language.** Three secrets have a default value written into the code:
`API_KEY_PEPPER`, `REDIS_IDENTITY_PEPPER` and `EMAIL_ENCRYPTION_KEYS`. The
repository is public, so anyone can read these values. If the server starts
without these variables set, it quietly uses the public values. Nothing refuses
to start. Any secret the server encrypts at rest would then be encrypted with a
key everyone knows: tenant SMTP passwords, webhook signing secrets, BYOK
provider keys. API-key hashes would be peppered with a known value.

**Evidence.** `backend/app/core/config.py:91-92` (peppers) and `:148`
(a valid-looking Fernet key). `_resolve_encryption_keys` (`:883`) only raises
when the value is empty, and the default is never empty. No validator checks
the peppers. Mitigation today: `docker-compose.prod.yml:55-58` uses `${VAR:?}`
and refuses to start without them, but only when you deploy with that exact
compose file. There is one more gap: `.env.production.template` ships
`API_KEY_PEPPER=` blank. A blank value overrides the default, which would give
an empty pepper with no error.

**Next step.** Phase 2: a test that builds `Settings(ENVIRONMENT="production")`
without these values must fail. Then fix it by refusing defaults and blanks in
production.

## F-004 — Server-side plan gating gaps to prove (P2, unverified)

**Plain language.** A locked item in the sidebar only hides a feature. The
server has to refuse the request too, or someone on a lower plan can call the
API directly and get a paid feature free. Most premium endpoints do call the
plan check (194 of them). The ones below do not, even though their page is
locked in the sidebar:

- `developer.py` (Developer platform, sidebar-locked by `developer_api`): 0 of
  6 handlers call a capability check. A code comment says the plan ceiling is
  enforced inside the service. Needs a test.
- `identity_admin.py` (Enterprise identity, Enterprise only):
  `PUT /identity/security-policy` and `POST /identity/domains/{id}/verify`
  have no check. The other 7 ungated handlers there are reads or deletes.
- `review_collab.py` (collaborative review, Enterprise only):
  `GET /review/collab/state` and the live WebSocket have no check.
- Reads and deletes in several gated modules have no check (API keys,
  webhooks, analytics, custom domains, branding, email settings). This is
  probably deliberate, so that a customer who downgrades can still see and
  delete what they built. **Product decision → NEEDS-OWNER N-003.**

**Evidence.** The static extraction of all 555 handlers (the `plan_required`
column in COVERAGE.csv). Rows marked `UNGATED in a <plan>-plan module`.

**Next step.** Phase 2: call each of these with a Free-plan token and expect 402/403.

## F-005 — Sidebar lock state does not match server enforcement (P3, unverified)

- "Analytics & BI egress" shows **no** lock, but every write behind it needs the
  `addon.warehouse_sync` grant (Business and above). A Free or Developer tenant
  sees an open page whose buttons fail.
- "Enterprise BYOK & models", "Partner marketplace", "Service levels" and
  "Data governance" have no plan gating anywhere (neither UI nor API). That may
  be intended. **Owner decision → N-002.**

## F-006 — Critical sweeps and backups depend on host cron (P2, unverified)

**Plain language.** Some important jobs are not started by the app or its
containers. They are started by a Linux "cron" file that must be installed on
the server by hand (`backend/deploy/cron.d/`). These are the data-retention and
erasure sweep (`compliance`), obligation reminders, ERP posting retries,
invitation and identity sweeps, and **the database backup and the restore
drill**. `docker-compose.prod.yml` does not run cron. If you deploy only with
containers (or on a PaaS), these jobs never run, and nothing tells you. Your
retention promises break silently and you have no backups.

**Evidence.** `backend/deploy/cron.d/flowpilot-sweepers` (17 entries),
`flowpilot-backups` (2 entries), `docker-compose.prod.yml` (no cron service).
Also, no producer was found in the app for the job types `billing.reconcile`,
`billing.assemble_invoice`, `usage.reconcile` and `billing.seat_sync`, apart from
scripts and gateway code paths. It is unverified whether anything enqueues them
on a schedule.

## F-007 — Repository hygiene (P3, confirmed from git)

- `backend/stripe.exe` (38 MB Stripe CLI binary) was committed in `d790047`
  and deleted in `1884891`. It is gone from the current tree but **still in git
  history**, so every clone downloads it. Removing it needs a history rewrite
  (force-push), which is the owner's call → N-005.
- `README.md` and `LICENSE` are empty (0 bytes). A public repo with no license
  is legally "all rights reserved". Choosing a license is the owner's call → N-004.
- `backend/requirements.txt` is UTF-16LE with CRLF line endings. It is a full
  Windows `pip freeze` (includes `git-filter-repo`, `colorama`, both
  `google-generativeai` and `google-genai`, and both torch and paddle stacks).
  pip reads UTF-16 with a BOM, but other tools (pip-audit, Dependabot, grep) may
  not. `requirements-dev.txt` contains only `pytest-env`.
- Historical one-off scripts: 5 `apply_hardening_*.py` + 20 `run_arch*.ps1` /
  `run_hardening*.ps1` at the root; 27 `apply_arch*.py` (up to 4 MB each) and
  30 `verify_*.py` in `backend/`; about 150 `verify_*`, `patch_*`, `fix_*`,
  `mutate_*` and `restore_*` scripts in `backend/scripts/`. Per CLAUDE.md these
  are not read or edited. Phase 2 moves them to `archive/` only if nothing
  imports or runs them. The CI job `backend-gates` runs
  `scripts/run_all_gates.py`, which probably runs many `verify_*` scripts, so
  most cannot be moved blindly.
- Large tracked files: `frontend/public/flowpilot-logo.png` and `favicon.png`
  are 1.37 MB each. That is heavy for a favicon.

## F-008 — Frontend route guards (P3, unverified)

`frontend/src/routes/RequireWorkspaceRole.tsx` is defined but never used. Under
`/organizations/:orgSlug/*`, `OrganizationGuard` checks membership and archived
status but not role. Pages such as Audit log, Service levels, Marketplace and
Autonomy rely on in-page checks or on the API returning 403. A MEMBER who
deep-links to `/organizations/x/audit` may see an error or an empty state
instead of a clear permission screen. The server still refuses the data, so
this is not a leak. Phase 3 renders every page as a forbidden role.

## F-009 — `/admin` has no index route (P3, unverified)

The platform shell `/admin` has only `margins`, `sovereign` and `revops`
children and no index route. A super admin who opens `/admin` probably sees
the platform layout with an empty body.

## F-010 — Frontend env template is wrong (P3, confirmed by static read)

`frontend/.env.example` lists `VITE_APP_NAME`, `VITE_APP_VERSION` and
`VITE_ENVIRONMENT`, and nothing in `src/` reads them. The only variable the code
reads, `VITE_API_URL` (`services/api/client.ts`), is missing from the template.

## F-011 — Most settings are undocumented for production (P3, unverified)

`backend/app/core/config.py` defines 302 settings. 246 of them are not in
`.env.production.template`, so their code defaults apply silently in
production. Defaults to review in Phase 2: `POSTGRES_PASSWORD='postgres'`,
`S3_DEV_FALLBACK_CREDENTIALS=True`, `SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT=True`,
`ENVIRONMENT='development'` (the app starts in development mode if the variable
is forgotten). Each has a ledger row (`config_var`).

## F-012 — WebSocket token in URL (P3, unverified)

`app/services/collab/gate.py:extract_token` reads the bearer token from
headers **or** from the query parameters `token`, `access_token`, `bearer` or
`auth`. Tokens in URLs end up in proxy and access logs. Check whether the query
form is actually used, and that logs redact it.

## F-013 — Three billing webhook entry points (P3, unverified)

Stripe events can arrive at `POST /api/v1/billing/webhooks/stripe` and
`POST /api/v1/billing/stripe/webhook` (both are the same handler in
`billing_webhook.py`), and at the generic `POST /api/v1/billing/webhooks/{gateway}`
(`billing_webhook_multi.py`). Stripe-specific wins for the literal path because
it is registered first. Phase 2 must prove, for every path, that there are no
unsigned or replayed events, that events are idempotent, and that out-of-order
events are handled.

## F-014 — Organization slug `request` collides with a public route (P3, unverified)

`/request/:token` is the public document-request upload page. Workspace URLs
are `/:orgSlug/:workspaceSlug`. The backend reserved-slug list
(`app/core/slugs.py`) does not include `request`. An organization named
`request` would have every workspace URL shadowed by the public upload page.

## F-015 — Sidebar and API disagree on ADMIN access (P3, unverified)

The sidebar shows Webhooks, Audit log, API keys and Enterprise identity only to
OWNER, while several of those endpoints accept `OWNER,ADMIN` (see
`roles_allowed` in the ledger). The most important case: **an ADMIN can create,
rotate and delete organization API keys through the API**
(`api_keys.py:53-174`, all `Depends(deps.RequireOrgAdmin)`). The comment in
`frontend/src/components/layout/navigation.ts` describes API keys as the
OWNER-only surface. Webhook endpoint management and audit-log read/export also
accept ADMIN. This is not a cross-tenant leak, but the UI and API disagree about
who may mint credentials. The owner should decide which one is right → N-006.
If OWNER-only is intended, this becomes a P2 authorization bug.

**Owner decision (2026-09-30):** OWNER and ADMIN may both create API keys. The
API is therefore right for API keys, and the sidebar is wrong to hide API keys
from ADMIN. Phase 4 makes the sidebar show API keys to ADMIN, with a test.
Webhooks, audit log and enterprise identity were not part of the decision and
stay as they are.

---

# Phase 1 findings (2026-09-30)

## F-016 — The backend test suite is red (P1, confirmed)

**Plain language.** There are 2,546 backend tests (2,538 before Phase 1 plus 8
new stub tests). Run as CI runs them, but without stopping early,
2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546. CI stops at the first 5 failures (`--maxfail=5`), so the
`pytest` job will stay red until these are worked through. Nothing in Phase 1
changed a test's expectations; the only test edit fixed an import path
(F-018 item 9).

**What the failures are (grouped by first error line; details in
`01-baseline.md`):**
- **Plan gating newer than the tests (≈40).** The API now answers
  `ADDON_REQUIRED` or `CAPABILITY_REQUIRED` (custom domains, warehouse sync,
  custom email, branding), but the tests' tenant is on a plan without those
  add-ons. Each needs a decision: fix the test fixture's plan, or treat it as
  a real regression.
- **API drift between code and tests (≈20).** Constructors changed signature
  (`AISettings(input_cost_per_1k_tokens=…)`, `LLMReservation(input_cost_per_1k=…)`,
  `FactSet(_facts=…)`).
- **Missing seed data in the test DB (≈50).** No published quota tier or price
  book is "in force" when a test needs one. The per-test TRUNCATE wipes them.
- **Order and harness dependence (≈25).** `relation "users" does not exist` in
  tests that use a separately named database, depending on what ran before.
- **Real behaviour gaps.** F-021 (secret echo) and SLO measurements written
  with a NULL `observed_value`. Automation timeouts report a different reason
  than the tests expect. Two IntegrityErrors that should be raised are not.
- **Sandbox only (4).** Model downloads blocked by the network (these pass
  where Hugging Face is reachable, as on GitHub runners).

**Next step.** Phase 3/4: triage each group. Fix the harness (seed data,
database naming) first, because it hides the real failures.

## F-017 — Model/migration drift; the drift gate could never fail (P2, confirmed)

**Plain language.** The database built by the migrations and the database the
Python models describe are not the same. `alembic revision --autogenerate`
proposes **314 operations**: 292 are indexes and constraints that migrations
created but the models do not declare (harmless at runtime, but autogenerate
would drop them). The other 22 matter more:
- table `organization_addons` exists but has no registered model (F-025);
- columns in the DB but not the model: `invoices.gateway`,
  `invoices.gateway_invoice_id`, `dunning_actions.gateway_event_id`,
  `invoice_line_items.created_at`;
- type mismatches: `automation_rules.conditions/actions` JSON vs JSONB; role
  columns that are Postgres enums in the DB but `Text` in the model
  (`enterprise_idp_configs.jit_default_org_role`,
  `idp_role_mappings.organization_role`, `scim_groups.workspace_role`);
  `redaction_*` CHAR(64) vs String(64); `sessions.idp_session_index` TEXT vs
  String(512); nullability of `document_chunks.content_tsv` and
  `document_settings.intent_config`;
- unique constraints the model declares but the DB lacks:
  `uq_ad_node_version`, `uq_scim_api_keys_key_prefix`,
  `uq_sso_auth_requests_request_id`.

**Why nobody saw it.** CI grepped `alembic/versions/ci_drift_probe.py`, but
alembic names the probe `ci_drift_probe_ci_drift_probe.py`. The grep never
found the file and the step always printed "No drift."

**Phase 1 action (CI only).** `backend/scripts/check_migration_drift.py`
replaces the inline step, and the 314 operations are listed in
`backend/alembic/drift_baseline.txt`. CI now fails on any new drift and on
any listed line that no longer drifts, so the list can only shrink. The drift
itself is **not fixed**. Zero drift means an empty baseline file.

## F-018 — A fresh clone could not install, migrate, test or start (P1, fixed)

Each item was proven failing, fixed in its own commit, and re-run.

1. **CI workflow rejected by GitHub:** see F-001.
2. **44 files with BOM/UTF-16:** see F-001.
3. **CI on Python 3.11, but `scipy==1.18.0` needs ≥ 3.12.** `pip install`
   failed ("No matching distribution"). CI now uses 3.12, matching
   `backend/Dockerfile`.
4. **CI set only `DATABASE_URL`, which the app ignores.** Settings builds the
   URI from `POSTGRES_*`, defaulting to `postgres:postgres`, while the CI
   database is `flowpilot:flowpilot`. CI now sets `POSTGRES_*`.
5. **`alembic upgrade head` refuses on every fresh database.** The last
   revision, `arch40_step3_contract_ai_settings`, raises unless
   `ARCH40_CONTRACT=1`. That blocked CI's migration job, the test suite's own
   setup, `start_dev.sh` and dev `docker compose up migrate`. Fixed for
   throwaway and dev databases only; production is F-024 / N-009.
6. **`start_dev.ps1` stopped 12 migrations short.** It upgraded to the pinned
   `arch40_step2a_review_view_paths`, so a fresh Windows setup had no tables
   for hm1 or ARCH-41..50. It now upgrades to head.
7. **`minio/minio` and `minio/mc` are gone from Docker Hub** ("object not
   found"), so `docker compose up` failed. Replaced by pinned `pgsty/minio` and
   `pgsty/mc` (a from-source rebuild of MinIO). → N-008.
8. **`run_all_gates.py` exited 2 before running any gate:** two gate names
   were neither `verify_arch<NN>` nor in `SPECIAL_ORDER`.
9. **A test imported a moved function** (`decrypt_password` from
   `app.core.smtp`). The collection error stopped the whole pytest run.
10. **The dev launchers never installed Python packages, never set
    `RERANKER_INTERNAL_TOKEN`** (compose refuses to start without it), **and
    never seeded plan tiers**: `seed_quota_tiers.py` refuses without gateway
    price ids, and both launchers reported success anyway.
11. **`./start_dev.sh` gave "Permission denied"** from a fresh clone: git
    stored it without the executable bit (mode 100644). Now 100755.
12. **`seed_price_book.py` crashed on the `.env` that `.env.example`
    produces.** Its own `.env` reader kept inline comments
    (`PLATFORM_SMTP_ENCRYPTION=TLS   # NONE | TLS | SSL`) and pushed them into
    the environment, so Settings validation failed. `reset_dev_environment.py`
    had the same reader. Both now use python-dotenv.
13. **`start_dev.sh` printed "(already seeded)" for any seed failure**, and
    `start_dev.ps1` printed `[ok]` regardless, so items 10 and 12 were
    invisible. Both launchers now show the failure.

**Fresh-clone proof (Linux).** `git clone` → `./start_dev.sh`, with no
`backend/.env`, `.venv` or Docker volumes present: all 8 steps `[ok]`, 1 price
book and 4 plan tiers seeded, `/api/v1/health` 200, and headless Chromium logs
in to the dashboard. A second run skips the package install, and both seed
scripts exit 0 when rerun. `start_dev.ps1` got the same changes but is
**unverified** (no Windows here).

## F-019 — OCR silently returns invented text on one error class (P1, unverified: code read)

**Plain language.** If the PaddleOCR engine raises an error that mentions
`ConvertPirAttribute2RuntimeAttribute` or `onednn_instruction` (a known
Paddle/CPU problem), `PaddleOCRProvider._run_engine`
(`backend/app/services/ocr/paddle.py`) does not fail. It returns a made-up
result with the text **"FLOWPILOT GATE INVOICE 12345"**, and that text is then
extracted, embedded and shown as the customer's document content. It looks
like a shim added so a verification gate could pass on a machine where Paddle
crashed.

**Next step.** Phase 2/4: a test that forces this error must see a failed OCR
job, not invented text. Remove the shim.

## F-020 — A chunk without a bounding box fails the whole document (P1, confirmed)

**Plain language.** `DocumentChunk.bbox` is `mapped_column(JSONB,
nullable=True)` without `none_as_null=True`, so SQLAlchemy stores Python `None`
as the JSON value `null`, not SQL NULL (checked: `none_as_null = False`). The
table's check constraint `ck_document_chunks_bbox_is_object` allows only SQL
NULL or a JSON object, so the insert fails and the entire enrichment job
fails. A missing box is a normal outcome in the real pipeline: the PDF
text-layer fallback emits pages with `blocks=[]`, OCR blocks without a polygon
get `box=None`, and a chunk that overlaps no block gets no box.

**Evidence.** With the first OCR stub, whose blocks had no boxes, every upload
ended `FAILED` with `CheckViolation … ck_document_chunks_bbox_is_object`
(worker log, 10 occurrences). With boxes present, the same uploads reach
`COMPLETED`.

**Next step.** Phase 4: a failing test that writes a chunk with `bbox=None`,
then `JSONB(none_as_null=True)` (or an explicit `sa.null()`), plus a check for
other nullable JSONB columns with the same mistake.

## F-021 — A rejected BYOK API key is echoed in the error response (P2, confirmed)

`PUT /organizations/{id}/byok/credentials` with an over-long key returns 422,
and the response body contains the full key under `"input"`. The existing
test `tests/api/test_byok_endpoints.py::TestCredentialConfidentiality::test_a_rejected_key_is_not_echoed`
fails for exactly this reason. FastAPI's default validation error handler
echoes input, and responses like this tend to be copied into logs, browser
devtools and error trackers.

**Fixed (Phase 2).** Root cause: FastAPI's default 422 handler returns pydantic's
error list, and every entry carries `input`, the value the caller sent.
`SecretStr` cannot prevent that, because the error is built from the raw input.
So the same leak applied to **every** field on every endpoint: passwords on
`/auth/register`, `/auth/reset-password` and `/auth/change-password`, reset
tokens, SMTP passwords, webhook secrets. A global `RequestValidationError`
handler (`app/core/exception_handlers.py`) now removes `input` from every 422
and keeps `type`, `loc` and `msg`, so no field needs to opt in. Proof: the
existing BYOK test plus 5 new tests in
`tests/security/test_validation_errors_do_not_echo_input.py`; 5 of them failed
before the change and all pass after. Nothing in the frontend or the tests read
`input`.

## F-022 — AI agent tool selector without tenant scope (P2, unverified)

Gate `verify_arch0v.py` 0V-G13: `agent_selectors.py:resolve_review_item` is a
registered tool selector with no `tenant` parameter. ARCH-13 required every
selector to carry a `TenantScope`. Phase 2 must prove whether an agent in
tenant A can resolve a review item from tenant B.

## F-023 — Client IP read from `X-Forwarded-For` in five places (P2, unverified)

Gate `verify_arch08_step6.py`: `core/client_ip.py`,
`services/identity/session_policy_service.py`, `api/v1/saml.py`,
`api/v1/byok.py` and `api/v1/scim.py` each read the header. Only the first
applies the `TRUSTED_PROXY_HOPS` rule. A client can forge the header to evade
IP-based rate limits, IP allow-lists or audit records. Phase 2: prove with a
forged header.

## F-024 — Production `migrate` fails on a fresh database (P2, confirmed: same command fails live)

`docker-compose.prod.yml` runs `alembic upgrade head` without
`ARCH40_CONTRACT`, so on a new production database the `migrate` container
exits with an error (F-018 item 5). Phase 1 left production unchanged on
purpose: when to run a lossy step on a real database is an owner decision →
N-009.

## F-025 — `organization_addon` model not registered (P3, confirmed)

Gate `verify_arch20.py` G10: the model module with a table is not imported by
`app/models/__init__.py`. That is why autogenerate wants to drop the
`organization_addons` table (F-017), and why relationships or metadata that
rely on the registry can miss it.

## F-026 — Storage errors surface as a raw 500 on upload (P3, confirmed)

When MinIO rejected the credentials, `POST /workspaces/{id}/work-items`
returned a bare 500 with an unhandled `StorageError` traceback in the log. A
storage outage should give the user a clear 503-style error and not leave a
half-created work item. (Seen live; the credential cause itself was a sandbox
artefact, F-027.)

## F-027 — Dev compose uses the shell's AWS key as the MinIO root user (P3, confirmed)

`backend/docker-compose.yml` sets `MINIO_ROOT_USER: ${AWS_ACCESS_KEY_ID:-minioadmin}`.
Docker Compose prefers the shell's environment over `backend/.env`. A
developer with real AWS credentials exported gets a local MinIO whose root
user is their AWS key id, while the app uses `minioadmin` from `.env`:
uploads fail with `InvalidAccessKeyId`, and the real key id is copied into a
container. The RUNBOOK documents the workaround. Phase 2: use MinIO-specific
variable names.

## F-028 — Four failing avatar requests on every dashboard load (P3, confirmed)

Headless Chromium after login: `GET /api/v1/users/{id}/avatar` returns 404
four times for a user without an avatar, and each logs a console error. Phase
3's fixture fails any test on console errors, so this must be fixed (don't
request an avatar the profile says does not exist, or return 204) before the
browser suite can be green.

## F-029 — 33 of 78 verification gates fail (P3, confirmed)

`run_all_gates.py --static-only`: 39 pass, 33 fail, 6 skip. Several failures
are stale gates that expect a pre-migration schema (`company_logo_url`),
require an argument (`verify_step4.py --phase`), or need rows in the database,
even though `--static-only` is supposed to avoid the database. Others are
real and are listed above (F-022, F-023, F-025) or in `01-baseline.md`. CI's
`backend-gates` job stays red until the gates are triaged. Gate scripts are
not read or edited under CLAUDE.md, so the triage needs an owner decision on
which gates are still authoritative.

## F-030 — The test harness is slow and order-dependent (P3, confirmed)

Every DB test runs `TRUNCATE … CASCADE` over about 200 tables twice (about
0.9 s each on a normal disk), so the suite takes about 2 hours serially on a
laptop-class disk and about 25 minutes on a RAM-disk Postgres. Some service
tests use a fixed database name (`flowpilot_svc_test`), so two runs at once
collide, and results change with test order (for example, no published tier
is "in force"). Phase 3: transaction-rollback isolation or a template
database, plus a unique database name per run.
