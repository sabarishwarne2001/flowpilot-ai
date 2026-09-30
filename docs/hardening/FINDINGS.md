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
| F-003 | P2 | **fixed** (Phase 2) | Config/secrets | Hard-coded default secrets are used if the env var is missing |
| F-004 | P2 | **fixed** (Phase 2; reads/deletes await N-003) | Plan gating | Some endpoints behind a locked nav item have no server-side plan check |
| F-005 | P3 | unverified | Plan gating / UX | Sidebar lock state does not match what the server enforces |
| F-006 | P2 | **fixed for Compose** (Phase 2; a real `docker compose` run on the VPS is unverified) | Jobs / ops | Retention, backup and 13 sweepers run only from host cron, which the prod compose file does not start |
| F-007 | P3 | **partly fixed** (Phase 2; history rewrite and script retirement await N-005, N-016) | Repo hygiene | Binary in history, empty README/LICENSE, UTF-16 requirements, ~250 historical scripts |
| F-008 | P3 | unverified | Frontend guards | Route-level role guard exists but is unused; org pages have no route-level role check |
| F-009 | P3 | unverified | Frontend | `/admin` has no index route (likely an empty screen) |
| F-010 | P3 | confirmed (static) | Config | `frontend/.env.example` omits `VITE_API_URL`, the only variable the code reads |
| F-011 | P3 | **partly fixed** (Phase 2; the long tail of harmless defaults is Phase 4) | Config | 246 of 302 backend settings are missing from `.env.production.template` |
| F-012 | P3 | **not a defect** (Phase 2; guard tests) | Auth | Live-review WebSocket accepts the access token in the URL query string |
| F-013 | P3 | **verified** (Phase 2; F-034 fixed) | Billing | Three webhook routes can receive Stripe events; each must be proven to verify signatures |
| F-014 | P3 | unverified | Tenancy URLs | Organization slug `request` is not reserved and collides with the public `/request/:token` page |
| F-015 | P3 | unverified | UI/API roles | Sidebar hides some pages from ADMIN that the API allows ADMIN to use |
| F-016 | P1 | confirmed (full pytest run) | Tests | The backend test suite is red: 2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546 |
| F-017 | P2 | confirmed (autogenerate) | Schema | 314 model/migration drift operations; the CI drift gate could never fail |
| F-018 | P1 | **fixed** (Phase 1, PR #2) | Startup / CI | A fresh clone could not install, migrate, test or start (13 blockers) |
| F-019 | P1 | **fixed** (Phase 2) | OCR | On one engine error class, OCR silently returns invented text |
| F-020 | P1 | **fixed** (Phase 2) | Ingestion | Any chunk without a bounding box makes the whole document fail |
| F-021 | P2 | **fixed** (Phase 2) | Secrets | A rejected BYOK API key is echoed back in the 422 response |
| F-022 | P2 | **not a defect** (Phase 2; guard tests) | Tenancy | An AI agent tool selector takes no tenant scope |
| F-023 | P2 | **not a defect** (Phase 2; guard tests) | Security | Five modules read `X-Forwarded-For` themselves (spoofable client IP) |
| F-024 | P2 | **mitigated** (Phase 2; production still needs N-009) | Deployment | Production `migrate` fails on a fresh database (ARCH-40 contract flag) |
| F-025 | P3 | confirmed (gate arch20 + drift) | Models | `organization_addon` model is not registered in `app/models/__init__` |
| F-026 | P3 | confirmed (live) | Uploads | Storage errors surface as a raw 500 on upload |
| F-027 | P3 | confirmed (live) | Dev env | Dev compose makes the shell's `AWS_ACCESS_KEY_ID` the MinIO root user |
| F-028 | P3 | confirmed (Playwright) | Frontend | Dashboard fires 4 failing avatar requests for users without an avatar |
| F-029 | P3 | confirmed (gate run) | Gates | 33 of 78 verification gates fail; several are stale |
| F-030 | P3 | confirmed (test runs) | Tests | The test harness is slow and order-dependent |
| F-031 | P3 | **fixed** (Phase 2) | Tenancy (IDOR) | Two document sub-routes answer 200 for another tenant's or an unknown document |
| F-032 | P2 | **fixed** (Phase 2) | Auth | An unverified account for someone else's address can list that address's pending invitations |
| F-033 | P3 | confirmed (test); open | Billing | Dodo's "test event reached a live deployment" guard compares the deployment's config with itself and cannot fire |
| F-034 | P3 | **fixed** (Phase 2) | Billing | `/billing/webhooks/STRIPE` (any capitalisation but lowercase) crashed with an unhandled AttributeError |
| F-035 | P2 | **fixed** (Phase 2) | Uploads | Page-level PDF actions (JavaScript, Launch, SubmitForm) survive the upload scrub into the stored, downloadable file |
| F-036 | P2 | **fixed** (Phase 2) | Uploads | An upload over the size limit crashed the upload endpoint with a 500 instead of answering 413 |
| F-037 | P3 | **fixed** (Phase 2) | Input handling | A NUL character (`%00`) in a search box crashed 19 routes with a 500 |
| F-038 | P2 | **fixed** (Phase 2) | Frontend | Production builds shipped source maps (`index-*.js.map`), publishing the original TypeScript |
| F-039 | P2 | **partly fixed** (Phase 2) | Dependencies | 7 npm advisories (fixed) and 13 Python packages with known advisories (4 bumped, rest tracked) |
| F-040 | P1 | **fixed** (Phase 2) | Deployment | The production API container cannot start: `gunicorn` is in no requirements file |
| F-041 | P2 | **fixed** (Phase 2) | Logs | gunicorn's access log wrote capability tokens (public upload and calendar-feed paths), query strings and the Referer to stdout |
| F-042 | P3 | **fixed** (Phase 2) | Ops | No readiness probe: `/health` reported "healthy" with Postgres down |
| F-043 | P3 | **fixed** (Phase 2) | Containers | Floating image tags (`pg16`, `7-alpine`, `2-alpine`, `python:3.12-slim`) |
| F-044 | P3 | **mitigated** (Phase 2) | Frontend/ingress | No Content-Security-Policy header |
| F-045 | P3 | open | Containers | Every image, including `web`, installs torch, paddle and sentence-transformers (about 8 GB) |
| F-046 | P3 | **fixed** (Phase 2) | Information exposure | Swagger UI and the full OpenAPI schema (every route and request shape) were public in production |
| F-047 | P1 | **fixed** (Phase 2) | Deployment / config | 23 settings the production template tells you to fill in never reached the containers (LLM keys, Dodo, billing gateway, price ids, token lifetimes, upload limit) |

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

**Fixed (Phase 2).** Proof of the bug and the fix on the same input
(`ENVIRONMENT=production`, `CORS_ORIGINS=*`, `LOG_LEVEL=DEBUG`,
`POSTGRES_PASSWORD=postgres`, `STORAGE_BACKEND=local`, rate limit off): the code
on `main` **booted**, using the public pepper and the public email key; the new
code refuses with 12 named problems. What changed:

- `app/core/production_guard.py` (new) and one validator in `Settings`. In
  `production` **and `staging`** the app refuses to start and lists every
  problem at once, by variable name, never by value: missing, blank, short,
  repetitive, placeholder or repository-published secrets (`API_KEY_PEPPER`,
  `REDIS_IDENTITY_PEPPER`, `EMAIL_ENCRYPTION_KEYS`, `JWT_SECRET_KEY`); the same
  value reused for two secrets; a default or trivial `POSTGRES_PASSWORD`;
  wildcard, non-https or localhost CORS; a non-https `FRONTEND_URL`; debug
  logging; rate limiting, login back-off, SAML wrapping defence or LLM
  metering switched off; an in-memory rate limiter; `STORAGE_BACKEND=local` in
  production; the public MinIO credentials; no `REDIS_URL`; no SMTP host
  (invitations and password resets would silently never arrive); a reranker
  without its token; Stripe or Dodo keys without their webhook secret, a
  wrong-mode publishable key, and live payment mode in staging.
- The three public defaults are **gone from `config.py`**. Development and
  test derive private values from the checkout's own `JWT_SECRET_KEY`, so
  nothing public can be the key and API keys survive a restart.
- `ENVIRONMENT` is normalised (`Production`, `prod`) and a misspelling is
  refused. Before, `ENVIRONMENT=Production` silently switched off every check
  that compares against the literal `"production"` (API-key prefix, SSRF
  production rules, internal-TLS rule, SFTP host checks).
- The refusal is its own exception, because a pydantic `ValidationError`
  prints the whole input dictionary, which here holds every secret.
- `.env.production.template` now names every variable the compose file and the
  guard require (it lacked `REDIS_URL`, the AI provider keys, session and
  upload limits and the migration flag) and a test proves it: copied as-is it
  is refused, with secrets filled in it boots, and it ships no usable secret.

Tests: `tests/core/test_production_config_guard.py` (72) and
`tests/infra/test_production_env_template.py` (21) pass. The
existing ML-stub, encryption-boundary and config tests pass; the only failures
in the neighbouring test files are ones already in the Phase 1 baseline.

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

## F-006 — Critical sweeps and backups depend on host cron (P2, fixed for Docker Compose)

**Plain language.** Some important jobs are not started by the app or its
containers. They are started by a Linux "cron" file that must be installed on
the server by hand (`backend/deploy/cron.d/`). These are the data-retention and
erasure sweep (`compliance`), obligation reminders, ERP posting retries,
invitation and identity sweeps, and **the database backup and the restore
drill**. `docker-compose.prod.yml` does not run cron. If you deploy only with
containers, these jobs never run, and nothing tells you. Your retention
promises break silently and you have no backups.

**Why the existing files could not work on your VPS.** Both scripts assumed a
Python virtual environment and Postgres client tools installed *on the server*.
A Docker Compose VPS has neither: Python and Postgres live in containers.

**What changed (Phase 2).**

- `deploy/bin/flowpilot-sweep` accepts `FLOWPILOT_RUNNER`. Set it (in
  `/etc/flowpilot/sweepers.env`) to a `docker compose ... run --rm ... python`
  command and every sweeper runs inside the stack's own image. Unset, the wrapper
  behaves exactly as before.
- New `deploy/bin/flowpilot-compose-backup`: `pg_dump` inside the `db`
  container (so the client always matches the server), AES-256 encryption, a
  read-back **verification** (decrypt, then `pg_restore --list`; a backup that
  cannot be read back is deleted, not kept), a checksum, 7 daily + 4 weekly
  retention, and an optional off-host mirror command.
- New `deploy/bin/flowpilot-compose-restore-drill`: restores the newest backup
  into a scratch database, compares table row counts and the Alembic head with
  the live one, times it, and drops the scratch database.
- New `deploy/cron.d/flowpilot-compose-backups`: the schedule (nightly backup,
  weekly drill). Install it *instead of* `flowpilot-backups`.
- Both scripts can report to a dead-man's-switch monitor (a free Healthchecks.io check
  works; `HEARTBEAT_BASE` plus one id per job): success pings `<base>/<id>`, failure
  pings `<base>/<id>/fail`, and a run that never happens is the monitor's alarm. Cron
  output goes to a log file, not nowhere (`MAILTO` is empty in these files, so anything
  printed and not logged was lost). A monitor that cannot be reached never fails a backup.
- `docs/RUNBOOK.md` gained "First deploy", "Backups" and "Scheduled jobs".

**Evidence.** `tests/infra/test_compose_backup_scripts.py` (20 tests) runs the
scripts against a real PostgreSQL with real `pg_dump`, `pg_restore` and
`openssl`: a backup is encrypted, checksummed and readable only with the key; a
missing or empty key stops the run and writes nothing; a failing dump keeps
nothing; the restore drill restores into a scratch database and drops it; the
drill fails on a corrupted backup and when a core table comes back empty;
retention keeps 7 daily + 4 weekly; a failed mirror is reported while the local
backup stays; the mirror command receives the backup path; a sweep runs through
the configured container runner and a failing sweep is recorded as a failure;
every cron entry names a sweeper the wrapper knows; the compose cron file
schedules a nightly backup and a weekly drill; success, a failed dump, a missing
key, a failed mirror and a failed drill each ping the right monitor address (5 of the
7 monitor tests failed before the change; the other 2 are no-monitor and
unreachable-monitor controls). A mutation check (encryption removed from the
script) makes the encryption test fail.

**Not verified.** A real `docker compose exec` / `run` against the production
stack: this session has no Docker daemon that can pull images (Docker Hub rate
limit), so the container-side commands were exercised through the
`FLOWPILOT_PG_EXEC` and `FLOWPILOT_RUNNER` prefixes with a native Postgres, not
through Docker itself. The first run on the VPS must be watched (RUNBOOK,
"First deploy", step 6).

**Still open.** The three point-in-time-recovery entries in
`flowpilot-sweepers` (`dr-heartbeat`, `base-backup`, `pitr-drill`) need
PostgreSQL WAL archiving configured; a Compose deployment does not have it. The
RUNBOOK tells you to leave those three lines commented out. Point-in-time
recovery is an owner decision (RPO wanted: today the newest nightly backup, up
to 24 hours of data) → N-012. Also unverified: whether anything enqueues the job
types `billing.reconcile`, `billing.assemble_invoice`, `usage.reconcile` and
`billing.seat_sync` on a schedule (apart from scripts and gateway code paths).

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

**Phase 2 status.**

- *`stripe.exe`*: still in history (N-005: no force-push without your say-so). The root
  `.gitignore` now blocks `*.exe`, `*.msi`, `*.dll`, `*.bin`, `*.dmp` and key material
  (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`). `git ls-files` shows no
  tracked binary or key file today. **Unfixed**: the clone still downloads 36 MB.
- *README and LICENSE*: the README was written in Phase 1. `LICENSE` is now the
  proprietary, all-rights-reserved notice you chose (N-004), with "FlowPilot AI" as the
  holder until you give the legal name. Have a lawyer read it before you rely on it.
- *`requirements.txt`*: already plain ASCII since Phase 1. Its contents are unchanged
  (F-045 covers the size problem).
- *Historical scripts*: **not archived, because they are not unused.** 37 tracked
  `apply_*.py` are named by 14 of your PowerShell runners (`run_arch*.ps1`,
  `run_hardening*.ps1`), and the 107 tracked `verify_*.py` are executed by CI's
  `backend-gates` job through `scripts/run_all_gates.py`. Moving either would break your
  Windows workflow or the gate job. I did not read or edit them (CLAUDE.md, N-010).
  Retiring them is your decision → N-016.
- *Logo images*: unchanged (Phase 4).

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

**Phase 2 status.**

- `POSTGRES_PASSWORD='postgres'`: production refuses a weak or default database password
  (F-003, tests in `tests/core/test_production_config_guard.py`).
- `ENVIRONMENT='development'` when forgotten: the production compose file sets
  `ENVIRONMENT: production`, the spelling is normalised (`Production`, `prod`), and the
  guard treats `production` and `staging` alike.
- `S3_DEV_FALLBACK_CREDENTIALS=True` and `SMTP_ALLOW_PRIVATE_IN_DEVELOPMENT=True`: read
  from the code, both apply only when the environment is development
  (`config.py:1132`, `email_service.py:65`), so they are inert in production. No test pins
  this yet.
- The template now names every variable the compose file requires and the start-up guard
  checks, and F-047 made sure everything in it reaches the containers.
- **Still open:** about 230 other settings keep their code defaults and are not in the
  template. I did not review each one for an unsafe default; the ones that matter for
  security are covered by the guard (secrets, CORS, debug logging, protections that must
  stay on, storage, Redis, mail, billing consistency).

## F-012 — WebSocket token in URL (P3, unverified)

`app/services/collab/gate.py:extract_token` reads the bearer token from
headers **or** from the query parameters `token`, `access_token`, `bearer` or
`auth`. Tokens in URLs end up in proxy and access logs. Check whether the query
form is actually used, and that logs redact it.

**Phase 2 result: not a defect.** The code only names those four parameters in order to
*refuse* them: a token in the query string raises `LiveRefused` and the handshake is closed
(1008) without being accepted. The token travels in the WebSocket subprotocol list (what a
browser can set) or an `Authorization` header, and API keys are refused outright.
`tests/security/test_live_review_token_not_in_url.py` (13 tests) pins this; a mutation that
accepts a query-string token makes 8 of them fail. The Caddy access log also drops the
`Sec-Websocket-Protocol` header where the token rides.

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

**Fixed (Phase 2).** Tests in `tests/services/test_paddle_ocr_honest_failure.py`
drive the real provider with an engine that raises the two real Paddle error
messages: 5 of 6 failed before (the provider returned the invented line), all
pass after. The shim is removed; `_run_engine` now raises `OCRError` that says
the page was **not** read and what to do, and logs `ocr.onednn_pir_crash` at
error level. A tripwire test fails if the invented string ever reappears in the
provider.

**Why it existed (read under owner decision N-010, gate is already red).**
`verify_arch10_step9.py` check G6.2 draws exactly that phrase on a PNG and asks
the pipeline to OCR it. The shim answered with the same phrase for *any* image,
so the gate could pass on a machine where Paddle crashes. That gate therefore
proved nothing on such machines. It is not edited (N-010). On a machine whose
Paddle works, or with `ML_STUBS=true`, G6.2 exercises the real path; on a
crashing machine it now fails, which is the honest result.

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

**Fixed (Phase 2), part 1: the chunk box.** The failing test
(`tests/services/test_chunk_writer_bbox.py`) calls the real writer
`replace_document_chunks` with a chunk that has no box and reproduced the exact
production error (`CheckViolation … ck_document_chunks_bbox_is_object`). The
column is now `JSONB(none_as_null=True)`, so a missing box is SQL NULL. Both
tests pass, and the 19 vector tenancy tests still pass. Part 2 (the same
mistake on 35 other nullable JSON columns) is the next commit below.

**Fixed (Phase 2), part 2: the sweep.** Introspecting the model metadata found
**35 more nullable JSON/JSONB columns** with the same default (for example
`audit_logs.details`, `usage_events.details`, `automation_rules.flow_spec`,
`jobs.result`, `work_items.extracted_entities`, `document_verification_fields.resolved_value`).
Three of them (`usage_events.details`, `automation_rules.flow_spec` and
`extracted_table_cells.bbox`) carry the same `IS NULL OR jsonb_typeof(...)` style of
constraint, so they would have failed the same way on the first `None`. All 35 now
use `none_as_null=True`. NOT NULL columns are left alone on purpose: there a JSON
null is a legal value. The ORM already reads SQL NULL and JSON null both as `None`
and no code queries for JSON null, so no behaviour is lost. The DDL is
identical, and the drift ratchet still reports the same 314 known operations.
A structural test (`tests/models/test_nullable_json_columns.py`) fails on any
future nullable JSON column that forgets the flag; it failed listing all 35
before the change.

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

**Phase 2 status: mitigated.** The `migrate` service now reads `ARCH40_CONTRACT` from
the environment (default `0`), so production never runs the lossy step by accident and
staging can keep `1` (your N-009 answer for dev, test and staging). RUNBOOK section 9.2
step 6 is the one-time procedure: back up if there are customers, run
`ARCH40_CONTRACT=1 $COMPOSE run --rm migrate` once, leave the file at `0`. The step archives
the three dropped columns' values first, and its `downgrade` restores them. **Still open:**
you have not said whether a production database with real data exists (N-009), and the
procedure has not been run against real containers.

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

---

# Phase 2 findings (2026-09-30)

## F-031 — A foreign or unknown document id answers 200 on two sub-routes (P3, fixed)

**Plain language.** Every route that addresses a document by id should answer
"not found" when the document is not in your workspace, whether it belongs to
another customer or does not exist at all. Two did not: `GET
/workspaces/{ws}/work-items/{id}/entities` and `.../extraction-memory` answered
`200` with an empty result. No data crossed the boundary (both queries filter by
your workspace), so this is not a leak, but it broke the rule every other route
follows and would have hidden a future leak behind a friendly empty page.

**How it was found.** A new sweep (`tests/security/test_idor_child_entities.py`)
creates real objects in tenant B (16 collections: 12 through the API, plus work
items, API keys, automation rules and memberships through the ORM), then calls
every route that addresses a child of those collections from tenant A's own
organization, with B's ids. 47 child routes were probed (16 GET, 14 POST, 10
DELETE, 6 PATCH, 1 PUT) and these two were the only ones that answered.

**Fixed.** Both handlers now return `404 Document not found` unless the document
is in the URL's workspace. Six new tests (three per route: foreign, unknown, and
the control that a workspace's own document still answers 200): 4 failed before,
all pass after. The sweep and its control that B's rows are untouched afterwards
also pass.

## Multi-tenancy proof (Phase 2) — no cross-tenant leak found

- **Path-level (all 423 org- and workspace-scoped operations, both
  directions):** `tests/security/test_cross_tenant_sweep.py`. Tenant A's owner
  called tenant B's real organization and workspace ids with schema-valid bodies
  on every route and method; every answer was 401/403/404, none 2xx, none 5xx. A
  user in no tenant is refused everywhere; anonymous callers get 401/403
  everywhere. A **mutation control** turns the membership check off and the
  sweep must then find crossings (it does), so a passing sweep is not vacuous.
- **Object-level (IDOR):** F-031 above; 16 object collections, 47 child routes.
- **Platform routes:** 39+ super-admin operations, discovered from the
  dependency tree, refuse all tenant roles (`test_superadmin_only.py`).
- **Partner programme:** 25 routes with route-level "any signed-in user" refuse
  non-members and lower partner roles (`test_partner_isolation.py`).
- **Not covered here (honest limits):** object-level IDOR is proven for the 16
  object collections above (12 the API can create from a generated body, 4
  created through the ORM); the remaining collections need hand-built fixtures (Phase 3/4). SCIM,
  the public API-key gateway and the WebSocket are checked separately below.

## F-004 — resolved (Phase 2): five routes gated, the rest documented

Method: a real organization on the seeded FREE tier (and one on ENTERPRISE as the
control) calls **every route in every locked feature area** with a schema-valid
request (`tests/security/test_plan_gating_server_side.py`, 200+ operations across
26 areas). Every answer must be a plan refusal (HTTP 402 `CAPABILITY_REQUIRED` or
`ADDON_REQUIRED`) or be listed with a reason. The server answers **402, not 403**,
by design: "your plan does not include this" is the true statement.

**Two real bypasses (a FREE tenant succeeded):**
- `POST /organizations/{id}/developer/keys` → **201**: minted a public-API
  gateway key, while `POST /api-keys` (same key type) was refused. The module's
  comment claimed the plan ceiling was "enforced inside the service"; the
  capability itself was checked nowhere. Fixed (commit `4d8f24e`).
- `PUT /organizations/{id}/identity/security-policy` → **200**: the Enterprise
  session and IP policy was writable from any plan. Fixed (`b7b5f34`).

**Three routes with a missing gate that were not exploitable on FREE** (no object
can exist to act on) but belong to the same feature: `PATCH
.../developer/keys/{id}/tier`, `POST .../identity/domains/{id}/verify`, `POST
.../branding/sender-domain/verify`. Gated (`4d8f24e`, `b7b5f34`, `d9fc369`).

**Deliberately still open, pending owner decision N-003** (listed by name with
that reason in `ALLOWED_OPEN`, so a change is a one-line test edit): reads and
deletes of a paid feature's own data (API keys, custom domains, developer
console, identity config, branding, analytics destinations, webhooks, email
settings). Two more are open by design: the `/potential` upsell counts and the
email "test" routes, which use the tenant's own SMTP settings and send nothing
without them. A NEW ungated route in a locked area now fails the test.

**Not yet gated by anyone's decision (N-002):** BYOK, marketplace, service
levels, data governance and analytics reads have no plan gating in the API or the
UI; the sweep does not treat them as locked.

**Controls.** The ENTERPRISE tenant is refused on plan grounds on none of these
routes, so the gate is not a blanket refusal.

**Existing tests touched.** Three tests in `test_public_api_endpoints.py` issued
developer keys on an organization with no plan. Their arrangement now uses a
fixture that puts it on the seeded DEVELOPER plan; no assertion changed.

## F-022 — not a defect; guarded (Phase 2)

Gate 0V-G13 flagged `agent_selectors.resolve_review_item` because it has no
parameter named `tenant`. Reading the code: every selector takes `scope:
AgentScope` (organization, workspace and proposal ids) and only *builds* a typed
action. Tenancy is enforced where an action is *applied*:
`actions._execute` loads the review item with `workspace_id=proposal.workspace_id`
and cases with `Case.workspace_id == proposal.workspace_id`, and
`lock_proposal` filters by workspace, so a proposal in workspace A that names an
item id from workspace B finds nothing and is SUPERSEDED. Guard tests
(`tests/security/test_agent_selectors_carry_a_tenant_scope.py`) pin those facts.
**Unverified dynamically:** a full cross-workspace apply needs review fixtures
(Phase 3). The gate that flagged it is not edited (N-010).

## F-023 — not a defect; guarded (Phase 2)

The five "reads" are one parser and its callers. Only `app/core/client_ip.py`
splits the header, applies `TRUSTED_PROXY_HOPS` and takes the entry the trusted
proxy appended (the last), never the client-controlled first. The SAML route
hands the raw header to the same resolver; BYOK, SCIM, warehouse sync and custom
domains call `client_ip(request)`; three of the five hits in the gate are comments
or docstrings. No code reads `request.client.host` directly. With forged headers:
the real address always wins, 39 rotating forged values resolve to one client (a
per-IP rate limit cannot be evaded), a chain shorter than the trusted hops or a
garbage entry never becomes the client IP (rate limiting falls back to the proxy,
IP pinning refuses). `tests/security/test_forged_forwarded_for.py`, including an
AST check that no other module splits the header or reads the socket peer.
**Deployment note:** with `TRUSTED_PROXY_HOPS=0` behind a reverse proxy every user
shares the proxy's IP and the per-IP limits (600 requests a minute globally) would
throttle everyone together. `docker-compose.prod.yml` defaults it to 1 for Caddy.

## F-032 — Pending invitations were shown to an unverified account (P2, fixed)

**Plain language.** Registration deliberately never says whether an email address
is already taken (good: it stops people probing who has an account). The side
effect is that *anyone* can create an account for `victim@company.com` before the
real person does, and that account starts out "unverified". The list "my pending
invitations" looked up invitations by the address alone. So the squatter could
open it and read every invitation sent to the victim: the organization's name, the
role offered, the inviter's email address and the workspace names. No token and no
access to the victim's mailbox was needed. This is the "pre-hijacking" pattern.

**Proof.** `tests/security/test_invitations_and_unverified_accounts.py`: an
organization invites an address; an unverified account holds that address; before
the fix the response listed the organization, its role and the inviter's email.

**Fixed.** `list_invitations_for_user` returns nothing until the account's email is
verified. A genuine invitee loses nothing: accepting an invitation needs the
emailed token and itself verifies the address. Control test: a verified owner of
the address still sees the invitation. The 34 existing invitation tests pass.

## Authentication audit (Phase 2)

**Already covered by existing tests (2,000+ pass; nothing weakened):** refresh
rotation and reuse detection (replaying a rotated cookie kills the family), grace
window for racing tabs, logout, logout-all (in-flight access tokens die),
per-device revocation, another user's session is a 404, tokens are stored only as
hashes, verification/reset/email-change tokens are single-use and purpose-scoped,
a completed reset kills a link sent to a stolen mailbox, login back-off. Refresh
cookie: HttpOnly, SameSite=Lax, path-scoped to `/auth/refresh`, `Secure` in every
environment except development and test (`ENVIRONMENT` is now normalised, so a
different spelling cannot switch it off).

**Added (`tests/security/test_auth_token_attacks.py`, 28 tests, all pass):**
unsigned `alg: none` tokens, wrong-secret and tampered-payload tokens, HS384/HS512
signed with the right secret (only the configured algorithm is accepted), expired
tokens, wrong or missing `type`, missing expiry, tokens for a user that does not
exist, malformed subjects (SQL text, empty, nil UUID) and malformed
`Authorization` headers all answer 401 and never 500, all refusals return the
same body (the reason is not revealed), an unverified member reaches no tenant
data, a deactivated user is refused, a token issued before the user's revocation
cutoff is refused, an access token is not accepted as a refresh cookie.

**Registration and reset do not enumerate accounts:** registration answers 202
with the same message whether or not the address exists (and emails the existing
owner instead); F-032 was the one place that undid that.

**Not covered here (residual, Phase 3/4):** live rate-limit behaviour of login and
forgot-password against Redis (the limiter is disabled by the test harness and the
per-user token limit is unit-tested), MFA (the product has none), SSO/SAML
end-to-end (an XML-signature-wrapping rig exists from ARCH-28), OIDC state and
nonce handling, and invitation role-escalation edge cases beyond the existing
suite (OWNER is not invitable by design).

## F-034 — Stripe posted to the generic webhook route crashed (P3, fixed)

`POST /billing/webhooks/{gateway}` is the gateway-neutral receiver added for Dodo.
Stripe has its own literal route, registered first, so `/billing/webhooks/stripe`
never reaches it. But `normalize_gateway` is case-insensitive, so
`/billing/webhooks/STRIPE` or `/Stripe` did: the Stripe adapter has no Standard
Webhooks verifier, and every delivery raised an unhandled `AttributeError` (a 500).
It failed closed (nothing was trusted or stored), so it is not a hole, but a
Stripe dashboard endpoint typed with a capital would have looked like a permanent
Stripe outage. The generic route now answers 404 for Stripe (its endpoint is its
own path). Three tests (three spellings) failed before, pass after.

## F-033 — Dodo's mode guard cannot fire (P3, open, needs a real payload)

The generic receiver refuses "an event from the other environment" by comparing
`event.livemode` with `DODO_LIVEMODE`. For Dodo, `event.livemode` is set from
`settings.DODO_LIVEMODE` (`dodo_gateway.verify_webhook_signature`), so the two
values are the same variable and the check can never differ. A test-mode Dodo event
posted to a deployment configured live is stored. What actually separates the two
environments today is the signing secret, which differs per Dodo mode; a live
deployment carrying the test-mode secret (or the reverse) would accept the wrong
environment's events. **Not fixed here:** it needs a sample of a real Dodo event to
see whether the envelope carries a mode flag (Dodo is blocked in this sandbox).
**Mitigation now:** the production template says Dodo test and live keys and
secrets are separate; keep exactly one set per environment. Decision for Phase 4
once a payload is available.

## Billing webhook audit (Phase 2)

Tests: `tests/security/test_billing_webhooks.py`, 21 tests with real Stripe and
Standard-Webhooks (Dodo) signatures. **Stripe** (both literal paths): valid event
recorded once and a replay acknowledged as `duplicate: true` with no new row; no
header, garbage header, empty scheme, wrong secret, tampered body, stale timestamp
and a signature over a different body all answer 400 with the same generic message
and write no row; secret rotation (either of two secrets); oversized body refused
before any signature work (413); a test-mode event is refused by a live deployment
and the reverse; **no secret configured means nothing is trusted (500, so Stripe
retries)**; a verified body that is not an event is refused. **Dodo:** same set
with 401 and an empty body (no information), replay adds no row, id swapped after
signing refused, oversized body refused, no secret means 500.
**Observation, not a defect:** a Stripe signature with a timestamp in the future
verifies; that is the vendor SDK's own behaviour (it only rejects old ones), and a
forged future timestamp needs the secret.
**Not proven (residual):** out-of-order delivery, failed-payment dunning,
cancellation, downgrade and quota enforcement are decided by the reconciler jobs,
which fetch current state from the gateway; the gateways are blocked here. They
are on the Phase 3 list and in `02-security-deploy.md`.

## F-036 — An over-size upload was a 500, not a 413 (P2, fixed)

**Plain language.** The main upload endpoint (`POST /workspaces/{id}/work-items`)
is supposed to answer "413: file too large" when someone uploads more than the
workspace allows. The line that builds that answer named a constant that does not
exist in the installed web framework (`HTTP_413_PAYLOAD_TOO_LARGE`; the real name is
`HTTP_413_REQUEST_ENTITY_TOO_LARGE`), so the size check itself worked and then the
error handler crashed. Every oversize upload was an unhandled 500 with a traceback
in the log, and the console could not tell the user "too large". Nothing oversize
was stored, so this is a correctness and noise problem, not a data leak. Found by
the upload attack tests; nothing else in the code used the wrong name.
**Proof:** `test_an_upload_over_the_limit_is_413_and_stores_nothing` fails with
`AttributeError` before the change and passes after.

## F-035 — Page-level PDF actions survived the scrub (P2, fixed)

**Plain language.** Uploaded PDFs are rebuilt from their pages, which removes the
document-level scripts (`/OpenAction`, `/Names /JavaScript`); that part worked and
is tested. But a page keeps its own "additional actions" and its links, and a link
can carry a JavaScript, Launch (run a program), SubmitForm or ImportData (send data
to a URL) action, or a whole attachment. Those were copied into the stored file,
which colleagues then download and open in Acrobat or a browser. A low-privilege
member could plant a hostile PDF for an administrator to open. Defence in depth, not
a server compromise, hence P2.
**Fixed.** `_strip_page_active_content` removes `/AA`, dangerous annotation actions
(JavaScript, Launch, SubmitForm, ImportData, GoToR/GoToE, media, following any
`/Next` chain) and attachment and media annotations from each page before it is
copied, so the removed objects are unreachable and are not written at all. Ordinary
links and destinations are kept. **Proof:** four tests (one per action type) that
inspect the stored bytes failed before and pass after; a control test proves an
ordinary 3-page PDF still round-trips.

## File handling and injection audit (Phase 2, uploads)

`tests/security/test_upload_attacks.py` (49 tests) and
`test_upload_endpoint_filenames.py` (14). Result: the pipeline is sound. **Refused
(and pinned):** HTML, a Windows executable, a shell script, SVG, empty, null-filled
and truncated bodies named or declared as PDF; a ZIP called a PDF; a PDF declared as
PNG (mismatch, quarantined); an octet-stream declaration hiding an executable; a
script in a GIF comment (removed by the re-encode); encrypted PDFs; corrupt PDFs; a
60-page PDF over a 50-page limit; an image that declares 1.6 billion pixels in a few
hundred bytes. **Archives** (the bulk-import path, `ingestion/archive.py`): a
300 MB-inflating bomb refused from its directory alone; thirty entries at moderate
ratio refused in aggregate; 2,005 tiny entries refused; six traversal and absolute
names never become a member; a nested archive, executables, HTML, SVG, macro
documents inside an archive are not members; a fake ZIP is refused. **XML** (the ERP
formats): external entities, entity-expansion bombs, parameter entities, case and
spacing variants, a UTF-16-encoded DOCTYPE, oversized input are refused; XInclude is
not processed. **Filenames through the real endpoint:** eleven hostile names (path
traversal in both slash styles, absolute paths, a 400-character name, NUL, an HTML
tag, CR/LF, a right-to-left override) never change where the file is stored (key is
`<organization>/<random>`), nothing is written outside the storage root, and the
download response carries no injected header. A non-PDF body leaves no work item and
no stored file; a viewer cannot upload. **Presigned links:** exports and redaction
bundles use a fixed 15-minute lifetime the client cannot change; the generated URL
carries `X-Amz-Expires <= 900` and is scoped to one key.
**Not covered here (residual):** the multipart upload-session and batch/bulk paths
(they share `validate_spooled` and `archive.inspect`, exercised above, but their own
endpoints are Phase 3), virus scanning (none exists; an owner decision for
enterprise sales), and the Caddy request-size ceiling (checked with the container
audit).

## F-037 — `%00` in a search box was an unhandled 500 on 19 routes (P3, fixed)

**Plain language.** PostgreSQL cannot store the "NUL" character. If someone types it
(or sends `%00` in the address), the database driver raises a Python error while it
builds the query, no handler expects it, and the user gets an internal-server-error.
Any signed-in user could do this on 19 routes (work-item search, obligations,
entities, cases, assistant sessions, the review queue, ERP postings and more), which
fills the error log and would trip alerting. It is **not** SQL injection: every value
is passed as a bound parameter, which is exactly why it fails safely.
**Fixed.** A small ASGI middleware (`app/middleware/nul_guard.py`) answers 400 "NUL
characters are not allowed" for NUL in the path, the query string, and JSON or
URL-encoded bodies (small ones; it recognises the JSON `\u0000` escape but not an
escaped backslash followed by the letters `u0000`). Uploads (binary) are never
inspected. **Proof:** the sweep found the 19 routes; 8 of 11 tests in
`test_nul_byte_guard.py` fail with the middleware unregistered and all pass with it,
and the sweep now records zero crashes.

## Injection audit (Phase 2)

**SQL injection: none found.** `tests/security/test_injection_sweep.py` calls every
tenant-scoped GET route that takes a string query parameter (search boxes, filters,
sort keys, cursors; 20+ parameters) with 16 payloads each (quote-or-true, stacked
`DROP TABLE`, `pg_sleep`, LIKE wildcards, backslash, template markers, a log4j-style
string, script tags, NUL, 5,000 characters, path traversal, `ORDER BY` injection):
300+ probes, no 5xx after F-037, the users table intact afterwards, and a boolean
probe (`' OR '1'='1`, `quarterly' OR '1'='1`) does not widen a search.
**SSRF: the client is sound.** `tests/security/test_ssrf_hostile_destinations.py`
(64 tests): loopback, the cloud metadata address, private, link-local, carrier-grade
NAT, multicast and reserved ranges are refused in every notation an attacker uses
(dotted, decimal `2130706433`, hex `0x7f000001`, octal, short `127.1`, bracketed
IPv6, IPv4-mapped IPv6, NAT64, 6to4); a hostname that resolves to both a public and a
private address is refused; only validated addresses are returned for connecting;
non-https schemes, `file:`, `gopher:` and `javascript:` are refused before any
connection; and ten hostile webhook URLs are refused at creation through the real
API. Existing tests cover no-redirect-following, size and time caps.
**Not covered here (residual):** template injection in tenant-editable email and
branding templates, and prompt injection that steers an AI action into triggering an
automation. The automation engine is deterministic and typed (see
`02-security-deploy.md`); the AI tool selectors take ids and closed vocabularies and
never retrieved text (an import-time check refuses otherwise), which is the design
answer to prompt injection, but no test here drives a hostile document through the
assistant end to end (Phase 3/4, needs the real model or a recorded one).

## F-038 — Production builds shipped source maps (P2, fixed)

**Plain language.** The frontend was built with `sourcemap: true`, so `npm run build`
wrote a `.js.map` next to every bundle. A source map is the application's original
TypeScript source, comments included. Caddy serves the whole `dist` folder, so
`https://app.example.com/assets/index-<hash>.js.map` would have handed anyone the
readable source of the product, which makes finding bugs and business logic trivial.
**Fixed.** `sourcemap: false`. `frontend/scripts/check-no-sourcemaps.mjs` checks the
Vite config and (with `--dist`) the built folder for any `.map` file or
`sourceMappingURL`; it exited 1 before the change ("build.sourcemap is true"), exits 0
after, and runs in CI after the build. A real production build was made and `dist`
holds zero map files. **If you want maps for an error tracker:** build them in a
separate step and upload them, never into the served folder.

## F-039 — Dependency audit (P2, partly fixed)

**npm (frontend): 7 advisories (6 high), all fixed.** `npm audit fix` (no
major-version change, 14 packages) took `npm audit` from 7 to 0. The two on
production code were `react-router` and `react-router-dom` (an RSC-mode CSRF bypass;
this app uses client-side routing only, so it was not reachable, but it is fixed
anyway). tsc, lint and the production build pass after the update. CI now runs
`npm audit --audit-level=high` and fails on any new one.

**Python: 54 advisory entries in 13 of 194 pinned packages (pip-audit).** Bumped
(security fix, no conflict, affected tests pass with no new failure against the
Phase 1 baseline): `pypdf` 6.14.2 → 6.16.1 (five advisories; it parses **untrusted
uploaded PDFs**), `pyasn1` 0.6.3 → 0.6.4, `anyio` 4.14.1 → 4.14.2, `aiohttp` 3.14.1 →
3.14.3. **Not bumped, with the reason (these are the Phase 4 dependency items):**
- `starlette` 0.41.3 (13 advisories; fixed in 0.47.2 to 1.3.1) is pinned by `fastapi`
  0.115.6. Moving needs a FastAPI line upgrade and the full suite. It carries the
  multipart parser used by uploads, so this is the most important remaining one.
- `python-jose` 3.3.0 (algorithm-confusion and JWE-bomb advisories): the fix (3.4.0)
  pins `pyasn1<0.5`, which conflicts with the patched pyasn1. The app uses it for
  HS256 only with a fixed algorithm list and no JWE, so neither advisory is
  reachable; the clean fix is to migrate to PyJWT.
- `cryptography` 49 → 50 (major), `oauthlib` → 4 (major), `pyarrow` → 23, `torch`
  2.12.1 → 2.13.0, `setuptools` → 83: upgrade with the suite; none is on a request
  path that takes untrusted input except through the libraries above.
- `paramiko` 3.5.1 and `ecdsa` 0.19.2 have no fixed version published. `paramiko` is
  the ERP SFTP client (egress-checked, host key pinned); `ecdsa` comes with
  python-jose and is not used for HS256.
A `pip-audit` job now runs on every pull request as **advisory** (not required),
so the list stays visible.

## F-040 — The production API container could not start (P1, fixed)

**Plain language.** The production compose file starts the API with
`gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker ...`. Gunicorn is
not listed in `requirements.txt` (or anywhere else), and the Docker image installs
only what that file lists, so the `web` container would exit at once with
"gunicorn: executable file not found". Every other production service waits for
`web`, and Caddy waits for `web` to be healthy, so the whole stack would come up
with no API. It survived because nothing in CI ever runs the production command
(the dev compose uses `uvicorn --reload`).
**Proof.** `tests/infra/test_production_compose_commands.py` reads the compose file
and checks that every Python program it starts (`gunicorn`, `uvicorn`, `alembic`) is a
pinned requirement and importable, that the worker class and the `app.main:app`
target exist: the gunicorn check failed before, all 9 pass after.
**Fixed.** `gunicorn==26.2.0` is pinned (installed and imported here; the pinned
resolution has no conflict). **Unverified:** a real `docker compose up` of the
production stack (images cannot be pulled or built in this sandbox: Docker Hub
rate-limits and the image needs ~8 GB), so the first production deploy should run
`docker compose -f docker-compose.prod.yml --env-file .env.production config` and
then `up -d web` and watch `docker compose logs -f web` (RUNBOOK, "First deploy").

## Containers and infrastructure audit (Phase 2)

**Verified by reading and by rendering `docker compose config` with a filled-in env
file** (the daemon cannot pull images here, so nothing below was *run* in containers):

- **Non-root: yes.** Every Dockerfile target ends with `USER flowpilot`; the two that
  switch to root to create a cache directory switch back.
- **Ports:** only Caddy publishes ports (80, 443); Postgres, Redis, MinIO, the API and
  the reranker are on the internal network. The dev compose publishes database and
  MinIO ports, which is dev only.
- **Health:** the API had a liveness check only; F-042 adds readiness and the
  production `web` healthcheck uses it. Workers, the scheduler and the reranker have no
  health check (a worker has no port; a heartbeat file is a Phase 4 item).
- **Memory limits:** set on every service.
- **`/api/v1/internal/*` is not published** (Caddy answers 404); the TLS "ask" endpoint
  is only reachable from inside the network.
- **F-043 (pinned versions).** `pgvector/pgvector:0.8.1-pg16`, `redis:7.4.6-alpine`,
  `caddy:2.10.2-alpine` and `python:3.12.13-slim-bookworm` (each confirmed to exist on
  Docker Hub); a test fails on any floating third-party tag. Pinning by digest
  (`@sha256:...`) is the next step and is an operator action at release time.
- **F-044 (CSP).** Caddy sends HSTS (1 year, preload), `nosniff`, `X-Frame-Options:
  DENY` and a strict Referrer-Policy, but no CSP. A
  `Content-Security-Policy-Report-Only` header is added so violations are visible in
  the browser without breaking anything; rename it to `Content-Security-Policy` after
  one real session shows none. **Unverified in a browser** (Caddy could not be run here).
- **F-045 (image size, open).** `requirements.txt` is a full `pip freeze` and every
  target installs it, so the "zero ML dependencies" `web` image carries torch (with
  CUDA libraries), paddle and sentence-transformers. It slows every deploy, enlarges
  the attack surface and the disk bill. Fix (Phase 4): split requirements per target.
- **Redis has no password.** It is reachable only on the compose network, so this is
  defence in depth; adding `requirepass` is a Phase 4 item.
- **F-024.** The migrate service now passes `ARCH40_CONTRACT` through from the
  environment (default `0`, so production does not run the lossy step by accident;
  staging sets `1` per owner decision N-009). RUNBOOK "First deploy" has the exact
  one-time procedure. Production stays open until the owner answers whether a
  production database with real data exists (N-009).

## F-046 — The API's documentation and full route map were public in production (P3, fixed)

**Plain language.** FastAPI ships an interactive page (`/docs`), a second one
(`/redoc`) and a machine-readable description of the whole API
(`/api/v1/openapi.json`). By default all three are open to anyone, with no
sign-in. FlowPilot has hundreds of routes (partner, operator, billing, SCIM), and
that description lists every one with the exact shape of each request. It does not
break anything by itself, but it is a free map for an attacker, and nothing in the
product uses it: the public API's documentation is hosted elsewhere and the web app
never reads the schema. The production ingress even forwarded `/docs` and
`/openapi.json` to the API on purpose.
**Proof.** `tests/security/test_api_docs_not_public_in_production.py` boots the app
in a fresh interpreter per environment. Before the fix, with a complete production
(or staging) configuration, `/docs`, `/redoc` and `/api/v1/openapi.json` all
answered 200 (2 failed). After it, they answer 404, and the control (a development
boot still serves them) passes.
**Fixed.** `app/main.py` turns the three URLs off when `ENVIRONMENT` is `production`
or `staging` (the same set the start-up guard treats as hardened). Development and
test are unchanged, so the schema-driven security sweeps and the existing tests that
read `/api/v1/openapi.json` still run.
**Left as is.** `deploy/Caddyfile` still has `handle /docs*` and `handle
/openapi.json` blocks; the API now answers 404 behind them. They were not removed
because a historical gate script pins that file's content and gate scripts are never
edited (N-010). Harmless, and worth deleting when the gate is retired.

## F-047 — 23 production settings never reached the app (P1, fixed)

**Plain language.** Docker Compose gives a container only the variables listed under
its `environment:`. Anything else in `.env.production` is used to fill in the compose
file itself and then thrown away. `.env.production.template` asks you to fill in 70
variables; 23 of them were not in that list, so the app never saw them and quietly ran
on its built-in defaults. The important ones:

- `GROQ_API_KEY`, `GEMINI_API_KEY`, `LLM_PROVIDER`: with no platform key, every AI
  feature fails for any customer who has not brought their own key.
- `BILLING_GATEWAY` and every `DODO_*` setting: Dodo Payments could not be selected
  or configured at all (the default gateway is Stripe).
- `GATEWAY_PRICE_ID_DEVELOPER` / `_BUSINESS`: the plan-seeding command could not see them.
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `MAX_UPLOAD_SIZE`: the
  template says these are "written out so they are visible", but changing them did nothing.
- the other `BILLING_*`, `CUSTOM_DOMAIN_*` and `TLS_*` tuning values.

Nothing failed loudly. It also hid a safety check: the start-up guard refuses a Dodo API
key with no webhook secret, but it never ran because the container never got the key.
**Proof.** `test_every_setting_the_template_asks_for_reaches_the_containers`
(`tests/infra/test_production_env_template.py`) lists every template variable that no
service receives; it failed naming exactly the 23 above and passes now. Two variables are
exempt on purpose, with a control test that the exemption list only holds real template
entries: `IMAGE_TAG` (read by Compose itself) and `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`
(passed to the one-off seed command only, so a password is not in every container).
**Fixed.** `docker-compose.prod.yml` passes all 23 on, each with the code's own default.
Checked by rendering the file with `docker compose config` and starting the app from
exactly the environment the `web` service receives: it boots in production with
`BILLING_GATEWAY=DODO`, a Groq key, and a Dodo test-mode base URL derived from
`DODO_LIVEMODE=false`; with every optional value blank it also boots; and a Dodo key with
no webhook secret is now refused at start-up as intended.
**Side fix.** Forwarding a blank `GROQ_API_KEY=` gives the app the empty string, not
"unset", and `llm_service.py` only refused None: a blank key built a client with an empty
credential and failed at the first call with the provider's own error. Blank now reads as
"GROQ_API_KEY is not configured" (same for Gemini), with 6 tests (4 failed before).
**Follow-up.** The template listed two of the three price ids the plan seeder reads and
called Enterprise "sales-led", but `scripts/seed_quota_tiers.py` sells it self-serve and
refuses a paid plan with no id, so the documented first-deploy seed would have stopped on
`GATEWAY_PRICE_ID_ENTERPRISE`. Added to the template and to the compose file, with a test
that reads the seeder's own table (it failed before). The plan table and its prices
(Developer $49, Business $299, Enterprise $799 per seat per month) live in that script; I
did not change or judge them, and NEEDS-OWNER N-011 asks you to confirm them before launch.
**Not done.** No guard makes a platform LLM key mandatory: a deployment where every
customer brings their own key is legitimate. The RUNBOOK checklist says what happens
without one.

