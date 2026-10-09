# Hardening campaign — STATE

_Last updated: 2026-10-09 (live bug hunt: fuzzing, double clicks, dev-mode rendering)_

## Latest pass — live bug hunt: fuzzing, double clicks, dev-mode rendering (2026-10-09)
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


## Latest pass — budget-capped header polish (2026-10-09)
Branch `claude/sweet-hopper-l4varl`. The owner had ~1% of weekly usage for this run, so the live
stack was NOT stood up (the sandbox had no Python dependencies or pgvector build). Only change: ERP
posting and Process intelligence now open with the shared `PageHeader` (icon tile, eyebrow, title,
description underneath, actions on the right) instead of a description squeezed beside the title.
Verified: `tsc -b`, eslint on both files, `npm run build`. Unverified: the browser suite and a live
look (route titles `/ERP posting/` and `/Process intelligence/` in `e2e/support/routes.ts` still
match the same `<h1>` text).

## Current phase
**Phase 3 — Final commercial hardening: COMPLETE, in review.** Branch
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
Open: **N-033** (which plans include TruthMesh; provisionally Enterprise only) and **N-032**
(which plans include Batch operations; provisionally Business + Enterprise), if not yet decided.
Everything else in NEEDS-OWNER.md is decided. Previous release: **N-026** Stripe
(test mode) for launch, Dodo selectable; **N-027** Postmark before the first paying customer;
**N-028** `app.flowpilot.ai` / `admin@flowpilot.ai`; **N-029** no unbacked trust claims;
**N-030** seat price = plan card price; **N-031** local model at a declared zero.

## Next action (exact)
1. Owner: review and merge the live bug-hunt PR (branch `claude/jolly-keller-d0jscd`, F-208 to
   F-215). Decide N-032 and N-033 when convenient (provisional placements are in force and are not
   release blockers).
   Left from the bug hunt, not done: the 12 routes that really are `async def` (uploads, streams,
   webhooks) still do their database and storage work on the event loop after their last `await`.
   The double-click sweep did not freeze them (unverified in general, not proven safe), and a large
   upload to object storage pauses other requests while it is written. Moving that tail to the
   thread pool is the next performance step.
2. Small items noticed and left for a later pass: the main JavaScript chunk is 412 KB gzipped
   (split the largest vendor libraries); `idp_session_sync` is stored but nothing reads it (the
   console no longer shows it; drop the column or build the feature). From Phase 2, still open:
   the matching queue lists one case per copy of an invoice (grouping them is a product choice).
   (Done 2026-10-09: the ERP and process pages now use the shared `PageHeader`.)
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
One long session (continued after its context filled): the live stack with log watch across every
console, hub, admin page, role and plan; a crawler pass per seeded person and at phone and tablet
widths; every scheduled sweep by hand; 25 fixes each proven by a failing test; the console
elevation; one full backend run and one full browser run on a fresh database; the documentation.
The exact spend is not visible from inside the session; check your usage page.

## Environment notes (for the next session)
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
