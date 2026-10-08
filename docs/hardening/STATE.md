# Hardening campaign — STATE

_Last updated: 2026-10-08 (Phase 2 — enterprise processing and TruthMesh)_

## Current phase
**Phase 2 — Enterprise processing and TruthMesh: COMPLETE, in review.** Branch
`hardening/phase-2-enterprise-processing-and-truthmesh`; PR opened from it (link in the session
summary). Builds on Phase 1 (merged into `main`, PR #11).

- **Live defects (each with a failing test first): F-171 to F-181** (FINDINGS.md, "Phase 2").
  The heavy ones: F-171 a three-way match that compared no line was MATCHED and approvable;
  F-173 the payment-risk check never read `vendor_bank_account`, so a changed payee account
  went unflagged; F-178 in the review hub, "a" (assign to me) also accepted every disputed
  extracted value. Also F-172 amounts in rupees, F-174 internal vendor keys shown, F-175
  assistant citations (shared React key, squeezed drawer), F-176 documents named by an id
  fragment, F-177 double-counted process events, F-179 "Ready to save" on an empty rule,
  F-180 clause checks shown with no trigger, F-181 a held arrow key smearing redaction regions.
- **New module: TruthMesh** — the cross-document digital twin: linked documents, eleven kinds of
  cross-document conflict with decisions that survive rebuilds, what-if ripples, a risk cockpit,
  a discrepancy matrix and an audit export (migration `p9a1`, 12 routes, worker jobs, a page).
  $0 external cost: pgvector and the local embedding model. Plan placement is provisional:
  **N-033** (NEEDS-OWNER.md, Enterprise only for now).
- **Tier-1 views:** review hub (field diff grid, tab counts, severity stripes), workflows and run
  history (honest rule cards, run summary, a time axis per chain), forensic radar (severity
  tiles, findings grid, duplicate matrix), Redaction Studio (toolbar, zoom, shortcuts, grouped
  regions), process intelligence (top-to-bottom discovery map, activities in words, compact
  proposals), matching queue (vendor, numbers, currency), assistant citation drawer, ERP empty
  state.

## What is done
- **Phases 0 to 5, final release, production configuration & UI elevation, final systemic
  polish, live feedback & Tier-1 elevation, Phase 1 (document intelligence):** merged.
- **Phase 2 — enterprise processing and TruthMesh** (this branch): F-171 to F-181 fixed;
  TruthMesh built.
  - Backend suite (full): **3,479 passed, 0 failed, 9 skipped** (was 3,450 passed, 1 failed:
    28 new tests; the Phase 1 failure was fixed before merge).
  - Browser suite (full, fresh database, production preview, CSP, model stand-in): **360 passed,
    0 failed, 1 skipped** (by design: the provider-down test needs no model). Logs clean.
  - `npm run build`, both `tsc` projects, lint, `npm run check:self`, `check-no-sourcemaps
    --dist`, encoding check: clean. One Alembic head (`p9a1_truthmesh_engine`); migration up,
    down and up again checked.
  - `COVERAGE.csv` (1,396 rows): deep 360 → **380**, 14 TruthMesh rows added.

## Owner decisions in force (do not re-ask)
Open: **N-033** (which plans include TruthMesh; provisionally Enterprise only) and **N-032**
(which plans include Batch operations; provisionally Business + Enterprise), if not yet decided.
Everything else in NEEDS-OWNER.md is decided. Previous release: **N-026** Stripe
(test mode) for launch, Dodo selectable; **N-027** Postmark before the first paying customer;
**N-028** `app.flowpilot.ai` / `admin@flowpilot.ai`; **N-029** no unbacked trust claims;
**N-030** seat price = plan card price; **N-031** local model at a declared zero.

## Next action (exact)
1. Owner: decide N-033 (and N-032 if still open), review and merge the Phase 2 PR.
2. Small items noticed and left for a later pass: the matching queue lists one case per copy of
   an invoice (the copies are flagged duplicates; grouping them is a product choice); the
   corroborator's "Highest" column is a raw score (0.90); the ERP and process pages still put
   their description beside the title.
3. Carried over, still yours to do: Stripe test-mode prices and webhook secret (F-125), the
   Postmark server (N-027), roll the keys pasted into chats, the first deploy
   (`docs/RUNBOOK.md` §9), F-124 before the first SCIM customer, the Tailwind 4 move.

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
One long session (continued twice after its context filled): the live stack with log watch across
every Phase-2 page and role, eleven fixes each proven by a failing test, the TruthMesh module
(engine, API, worker, UI, 23 backend and 5 browser tests), the Tier-1 views, one full backend run and
one full browser run on a fresh database, and the documentation. The exact spend is not visible from
inside the session; check your usage page.

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

