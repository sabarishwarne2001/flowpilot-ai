# Hardening campaign — STATE

_Last updated: 2026-10-08 (Phase 1 — document intelligence)_

## Current phase
**Phase 1 — Document intelligence: COMPLETE, in review.** Branch
`hardening/phase-1-document-intelligence`; PR opened from it (link in the session summary).
Builds on the live-feedback release (merged into `main`).

- **Live defects (each with a failing test first): F-155 to F-170** (FINDINGS.md, "Phase 1").
  The heavy ones: F-163 a currency written in words ("US Dollars") stopped every engine after
  extraction for that document; F-162 the bulk CSV export let a document plant a spreadsheet
  formula; F-160 one refused file silently dropped the rest of a multi-file upload; F-158
  "Duplicate detection" was a setting nothing read; F-155 filtered lists reported the wrong
  total; F-164 paying customers saw an upgrade message while pages loaded; F-167 cases and
  packet review offered actions the role could not take.
- **Tier-1 views:** document workbench (zoom, pan, page jump, per-field confidence), tables
  (sticky header, column sums against the total row, grouped export), overview (Failed KPI,
  document types from the classifier, compact upload), Cases board, entity graph (readable
  labels, find a record), Documents on a phone.
- **New module: Batch operations** — batches, confidence analytics, schema self-healing with
  undo, dispatch lanes, SHA-256-verified export packages (migration `p8a2`, 19 routes, a page
  and a detail page). Plan placement is provisional: **N-032** (NEEDS-OWNER.md).

## What is done
- **Phases 0 to 5, final release, production configuration & UI elevation, final systemic
  polish, live feedback & Tier-1 elevation:** merged.
- **Phase 1 — document intelligence** (this branch): F-155 to F-170 fixed; Batch operations built.
  - Backend suite (full): **3,450 passed, 1 failed, 9 skipped** (was 3,366 passed: 84 new
    tests). The failure was the storage-boundary source guard reading a method named
    `write_bytes` on the in-memory package writer; renamed (`8b4eac7`), the guard and the batch
    suites pass (69). No other backend file changed after the full run.
  - Browser suite (full, production CSP, model stand-in, on the long-lived local database):
    **333 passed, 5 failed, 1 skipped.** One failure was real and is fixed (F-170, re-run: 13/13
    in `30-auth`). The other four are data only: that database holds a second copy of each sample
    document, uploaded by the seed bug F-168 (now fixed); those tests expect exactly the two
    sample invoices and pass on a fresh database. See "Next action" 1.
  - `npm run build`, both `tsc` projects, lint, `npm run check:self`, `check-no-sourcemaps
    --dist`, encoding check: clean. One Alembic head (`p8a2_batch_dispatch_engine`); drift
    check: no new drift (283 known operations).
  - `COVERAGE.csv` (1,382 rows): deep 328 → **360**, untested 395 → **394**.

## Owner decisions in force (do not re-ask)
Open: **N-032** (which plans include Batch operations; provisionally Business + Enterprise).
Everything else in NEEDS-OWNER.md is decided. Previous release: **N-026** Stripe
(test mode) for launch, Dodo selectable; **N-027** Postmark before the first paying customer;
**N-028** `app.flowpilot.ai` / `admin@flowpilot.ai`; **N-029** no unbacked trust claims;
**N-030** seat price = plan card price; **N-031** local model at a declared zero.

## Next action (exact)
1. Browser suite on a fresh database (the four data-only failures above): drop and recreate
   `flowpilot_e2e` (Environment notes, "Fresh e2e database"), `start-stack.sh`, then
   `E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e`. CI runs on a fresh database already.
2. Owner: decide N-032, review and merge the Phase 1 PR.
3. Carried over from the previous release, still yours to do: Stripe test-mode prices and
   webhook secret (F-125), the Postmark server (N-027), roll the keys pasted into chats, the
   first deploy (`docs/RUNBOOK.md` §9), F-124 before the first SCIM customer, the Tailwind 4 move.

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
One long session (continued once after its context filled): the live stack, a browser and log
watch across every Phase-1 page and role, sixteen fixes each proven by a failing test, the Batch
operations module (backend, worker, UI, 64 backend and 4 browser tests), the Tier-1 views, one
full backend run and one full browser run in parallel on one Postgres server, and the
documentation. The exact spend is not visible from inside the session; check your usage page.

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
- The browser-suite seed now finds its sample documents by name (F-168); before that, a database
  with more than 50 documents in Tenant C received a second copy of each sample on every run.
- Ad-hoc screenshot probes (not part of the suite) can be written under `frontend/e2e/tools/`
  and run with `E2E_TEST_DIR=./tools`; this phase's probes were kept out of the repository.
- `seed_price_book.py --version auto` publishes the next price book only when the seed changed;
  the start scripts and the e2e seed use it.

