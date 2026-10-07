# Hardening campaign — STATE

_Last updated: 2026-10-07 (final systemic polish and live engine hardening)_

## Current phase
**Final systemic polish & live engine hardening — COMPLETE, in review.** Branch
`hardening/final-systemic-polish-and-live-engine-hardening` (session branch
`claude/wonderful-gates-5fid79`); PR: see "Next action". Builds on PR #8 (merged into `main`).

- **Live engine defects.** F-128 (knowledge reindex crashed every job on the spend guard),
  F-129 (a later reindex queued nothing), F-126 (two tabs refreshing signed each other out),
  F-130 (sign-out never reached the server), F-127 (offset-paged lists without a stable order,
  18 of them; the review hub stuck on an emptied page; the flaky browser test).
- **Live exploratory sweep.** The real stack (API, worker loop, Redis, Postgres, model stand-in,
  production bundle with the production CSP): a crawler clicked every non-destructive button on
  all 39 pages (populated workspace) and all 21 workspace pages of a brand-new empty workspace;
  305 parameter-free GETs on populated, second and empty workspaces (zero 5xx); every named
  secondary action triggered by hand. API and worker logs: zero tracebacks. Jobs table: every
  background job the live system ran SUCCEEDED (33 job types, none failed or dead). Found and
  fixed F-138 (DPA export not downloadable on local storage); noted N-030 (seat price) and N-031
  (local-model price alerts).
- **Lifecycle and roles.** Archive is now reversible and enforced end to end: F-131 (workspace
  restore impossible; archived workspaces in members' switchers), F-132 (no organization restore),
  F-133 (sign-in into an archived organization), F-134 (archived tenants kept their background
  work, billable sweeps and data exports included). The picker shows archived tenants apart with
  Restore. Both members pages explain organization roles vs workspace roles.
- **UI.** Softer light theme (zinc canvas, white cards), F-135 (theme toggle from "system", white
  flash on dark), F-136 (stale profile picture/logo), F-137 (eleven dialogs ignored Escape and let
  focus escape), "Only if" conditions styled like code, Run history with status dots, rule names,
  duration badges and node timelines. The tenant self-checks now run in CI (`npm run check:self`).
- **Verified:** see "What is done".

## What is done
- **Phases 0 to 5, final release, production configuration & UI elevation:** PRs #1 to #8 (merged).
- **Final systemic polish & live engine hardening** (this branch): F-126 to F-140 fixed, each with
  a test that failed first (FINDINGS.md). Owner items N-030 (seat price), N-031 (pricing a
  self-hosted model).
  - Browser suite on a fresh database, production CSP, model stand-in: **302 passed, 0 failed,
    1 skipped** (by design; was 290 passed: 12 new tests).
  - Backend suite: BACKEND_RESULT.
  - `npm run build`, both `tsc` projects, lint, `npm run check:self` (new), `check-no-sourcemaps
    --dist`, encoding check: clean. One Alembic head (`p6a3_user_mfa_factors`), no migration added.
  - `COVERAGE.csv` (1,347 rows): deep 305 → **317**, untested 418 → **397** (21 background jobs
    now have live evidence from the jobs table), 2 new routes.

## Owner decisions in force (do not re-ask)
All of Phase 5's (N-004, N-006 to N-014, N-019 ERP part, N-020 items 1/4/6, N-021 to N-025) plus,
from this release: **N-002** service-level targets Enterprise; **N-003** read + delete after a
downgrade; **N-005** no history rewrite; **N-015** CSP enforced; **N-016** scripts archived;
**N-017** MFA, Redis password, uptime heartbeat, image pinning; **N-018** 20 sign-ins per address
per 5 minutes; **N-019** viewers do not see Workflows / Run history / Review queue; **N-020** all
seven capabilities built. Details: NEEDS-OWNER.md, "Final release".

## Next action (exact)
1. Owner: review and merge the PR for this branch (screenshots of the new picker, Run history,
   workflow conditions and role guide were sent with the session summary).
2. Owner decisions N-030 (per-seat price in the price book), N-031 (pricing a self-hosted model),
   and the earlier N-026 to N-029.
3. Stripe Dashboard (test mode): the webhook endpoint and its signing secret in `.env.production`
   (F-125); the app refuses to start until then.
4. Before the first deploy, regenerate the server secrets and roll the keys pasted into a chat
   (R2 token, Gmail app password, Groq keys); test the Gemini `AQ.` key from your own machine.
5. Then the first deploy: `docs/RUNBOOK.md` §9.
6. Before the first customer configures SCIM: F-124 (SCIM token pepper).

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh` (pgvector
0.8.0 built from source: `apt-get install postgresql-server-dev-16`, then `make && make install` in a
v0.8.0 checkout), `backend/.env` from `.env.example` plus the block in `frontend/e2e/README.md`,
`python3.12 -m venv backend/.venv && pip install -r requirements.txt -r requirements-dev.txt`,
`npm ci` in `frontend`, then `E2E_LLM=1 frontend/e2e/scripts/start-stack.sh` and
`E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e`.

## Blockers
None in engineering. Not verifiable from this environment: the production compose stack on a real
server (F-006) and the CPU-only torch image (F-045).

## Budget notes
One session: environment set-up, the fixes with their tests, a live click-through crawl (two
runs), API sweeps, one full browser run and one full backend run (in parallel on two RAM-disk
Postgres clusters, which slows the backend run to well over an hour), and the documentation. The
exact spend is not visible from inside the session; check your usage page.

## Environment notes (for the next session)
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
- Model hosts, Stripe, Groq and Dodo are blocked; PyPI and npm are reachable.
- **Live sweeps**: the crawler and API sweep scripts used in this pass are described in
  FINDINGS.md ("Final systemic polish"); the jobs table (`select job_type, status, count(*) from
  jobs group by 1,2`) is the record of every background job the live stack ran, and survives the
  log rotation that `start-stack.sh` does on every restart.
- The browser suite reuses a preview server on port 3000 if one is running (outside CI), so a run
  started while another is serving tests the older bundle.
