# Hardening campaign — STATE

_Last updated: 2026-10-07 (live feedback and Tier-1 elevation)_

## Current phase
**Live feedback & Tier-1 elevation — COMPLETE, in review.** Branch
`hardening/live-feedback-and-tier1-elevation`; PR: see "Next action". Builds on PR #9 (merged
into `main`).

- **Live UI feedback (owner's report).** F-141 reindex status route and a polling progress card
  (button disabled while a run is active, start and finish toasts); F-142 ARCHIVED badge in "Your
  workspace access" (archived workspaces and archived organizations); F-143 every avatar, logo
  and page image falls back to initials / a message instead of a broken-image icon (the profile
  page used to refetch a bad picture in a loop).
- **Owner decisions N-026 to N-031: all decided** (NEEDS-OWNER.md). N-030 per-seat prices
  ($49/$299/$799) and N-031 the local model at a declared zero are implemented in the price book
  seed, with `--version auto` so existing servers pick them up.
- **Audit fixes (each with a failing test first):** F-144 page-level error boundary and
  stale-bundle reload; F-145 twenty silent mutations now report failure; F-146/F-147 two
  scheduled sweeps that nothing ran (stranded automation runs, stranded email deliveries); F-148
  expired invitations; F-149 bulk document actions were unreachable; F-150 a rule that had run
  could not be deleted (soft delete, migration `p7a1`); F-151/F-152 deleting a document or erasing
  a person left the files in storage; F-153 member names and pictures; F-154 tab titles.
- **Verified:** see "What is done".

## What is done
- **Phases 0 to 5, final release, production configuration & UI elevation, final systemic
  polish:** PRs #1 to #9 (merged).
- **Live feedback & Tier-1 elevation** (this branch): F-141 to F-154 fixed; N-026 to N-031 decided.
  - Backend suite (full): in progress at this commit; result recorded when it completes.
  - Browser suite on a fresh database, production CSP, model stand-in: **316 passed, 0 failed,
    1 skipped** (by design; was 302 passed). The 12 tests in the new `21-live-feedback.spec.ts`
    each failed on the previous code before its fix.
  - `npm run build`, both `tsc` projects, lint, `npm run check:self`, `check-no-sourcemaps
    --dist`, encoding check: clean. One Alembic head (`p7a1_automation_rule_soft_delete`); drift
    check: no new drift. `npm audit`: 5 high remain, all Tailwind 3 build tooling (FINDINGS).
  - `COVERAGE.csv` (1,353 rows): deep 317 → **328**, untested 397 → **395**.

## Owner decisions in force (do not re-ask)
Everything in NEEDS-OWNER.md is decided; nothing is open there. This release: **N-026** Stripe
(test mode) for launch, Dodo selectable; **N-027** Postmark before the first paying customer;
**N-028** `app.flowpilot.ai` / `admin@flowpilot.ai`; **N-029** no unbacked trust claims;
**N-030** seat price = plan card price; **N-031** local model at a declared zero.

## Next action (exact)
1. Owner: review and merge the PR for this branch.
2. Stripe Dashboard (test mode): the three per-seat prices into `GATEWAY_PRICE_ID_*`, the webhook
   endpoint and its signing secret in `.env.production` (F-125; the app refuses to start without).
3. Postmark: create the server, put its SMTP token and the `noreply@flowpilot.ai` sender in
   `PLATFORM_SMTP_*`, publish its SPF/DKIM records (N-027).
4. Before the first deploy, regenerate the server secrets and roll the keys pasted into a chat
   (R2 token, Gmail app password, Groq keys); test the Gemini `AQ.` key from your own machine.
5. Then the first deploy: `docs/RUNBOOK.md` §9 (DNS A record for `app.flowpilot.ai` first, N-028).
6. Before the first customer configures SCIM: F-124 (SCIM token pepper).
7. When convenient: the Tailwind 4 migration clears the remaining build-time npm advisories.

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh` (pgvector
0.8.0 built from source: `apt-get install postgresql-server-dev-16`, then `make && make install` in a
v0.8.0 checkout), `backend/.env` from `.env.example` plus the block in `frontend/e2e/README.md`
(and `POSTGRES_PORT=5433`, `POSTGRES_DB=flowpilot_e2e`, the CI secrets from `.github/workflows/ci.yml`),
`python3.12 -m venv backend/.venv && pip install -r requirements.txt -r requirements-dev.txt`,
`npm ci` in `frontend`, then `E2E_LLM=1 frontend/e2e/scripts/start-stack.sh` and
`E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e`. Backend tests: the CI environment block
(`POSTGRES_*` pointing at port 5433, `ARCH40_CONTRACT=1`, the CI secrets), then `pytest -q`.

## Blockers
None in engineering. Not verifiable from this environment: the production compose stack on a real
server (F-006) and the CPU-only torch image (F-045).

## Budget notes
One session: environment set-up (pgvector build, Python and npm installs), the fixes with their
tests (each proven failing on the previous code first), screenshots of the main pages, one full
backend run and one full browser run (in parallel on one Postgres server, which slows both), and
the documentation. The exact spend is not visible from inside the session; check your usage page.

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
- **Fresh e2e database**: `psql -h localhost -p 5433 -U postgres -c "DROP DATABASE flowpilot_e2e WITH (FORCE)"`
  then `CREATE DATABASE flowpilot_e2e` and `start-stack.sh` (migrates). Some browser tests create
  organizations; archived ones count towards an account's limit of three, so a database that has
  seen many runs eventually refuses new ones.
- `seed_price_book.py --version auto` publishes the next price book only when the seed changed;
  the start scripts and the e2e seed use it.

