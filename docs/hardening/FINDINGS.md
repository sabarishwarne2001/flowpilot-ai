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
| F-001 | P1 | confirmed (GitHub API + local run) | CI | CI on `main` fails instantly with zero jobs; the encoding gate would also fail (44 files) |
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

---

## F-001 — CI on `main` fails instantly with zero jobs (P1, confirmed)

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
