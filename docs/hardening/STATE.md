# Hardening campaign — STATE

_Last updated: 2026-10-05 (end of Phase 3)_

## Current phase
**Phase 3 — Browser test harness and coverage: COMPLETE. Stopped at the Phase 3 checkpoint.**
PR: PR_LINK_PLACEHOLDER (branch `hardening/e2e`). The next session starts Phase 4 after you have
read the PR and answered N-019 and N-020. Do not redo Phases 0 to 3.

## What is done
- **Phase 0:** `00-map.md`, `COVERAGE.csv`, `FINDINGS.md`, `NEEDS-OWNER.md` (PR #1, merged).
- **Phase 1:** boot and honest baseline (PR #2, merged).
- **Phase 2:** security and deployment blockers (PR #3, merged); full report `02-security-deploy.md`.
- **Phase 3** (this branch; full report `03-coverage.md`):
  - Owner decisions N-009, N-011, N-012, N-014 (approved), N-016 recorded.
  - Playwright harness in `frontend/e2e` with a strict failure fixture, fresh session per test,
    idempotent seed (`backend/scripts/seed_e2e.py`), real sample documents, a local SMTP sink, stack
    scripts, a known-issue registry, inventory tools, README; advisory `e2e` CI job.
  - **275 browser tests; final full run 233 passed, 40 failed, 2 skipped** (10.4 min). All 40
    failures are product findings; the 2 skips need an LLM key.
  - **F-050 (P0) fixed**: concurrent packet thumbnails aborted the whole API (PDFium thread safety);
    regression test red 3/3 before, green 3/3 after. Full backend suite NOT re-run after it.
  - New findings F-049 to F-066; the worst open one is **F-051 (P1)**: accepting a team invitation
    returns 500 on every organization with a live subscription.
  - `COVERAGE.csv`: 147 rows moved by passing/failing browser tests; ledger now 55 `deep`,
    287 `smoke`, 967 `untested` (endpoints, jobs and integrations untouched).

## Owner decisions in force (do not re-ask)
- **N-004** proprietary, all rights reserved ("FlowPilot AI" until the legal name is given).
- **N-006** OWNER and ADMIN may both create API keys (the sidebar fix is Phase 4, F-015).
- **N-007** production is a Linux VPS running Docker Compose.
- **N-008** keep the pinned community image `pgsty/minio` (which includes `mc`).
- **N-009** `ARCH40_CONTRACT=1` for dev, test and staging. Production is still open: the flag is
  passed through, default 0, and the runbook has the one-time procedure.
- **N-010** the campaign MAY READ failing `verify_*.py` gates to diagnose them but must NEVER edit,
  weaken, delete or skip a gate script. No gate may be retired without the owner. `apply_*.py`,
  `backend/evidence/`, `arch07_*`, `arch08_*`, PDFs and certification files stay off limits.

## Next action (exact)
Phase 4 (fix loop), in this order:
1. Run the full backend suite on `hardening/e2e` and compare with `main` (F-050 Evidence Rule step 4).
2. **F-051** (P1): add `billing.seat_added`/`billing.seat_removed` to the outbox visibility
   vocabulary by migration (one head), with a failing test first; check `jit_service` and
   `deprovision_service`, which emit the same event. Then re-run `tests/20-organization.spec.ts`.
3. **F-052** (audit export format), **F-056** (assistant keeps the question), **F-066** (PDFium
   lock at the other call sites, stress test each); **F-053/F-065** once N-019 is answered.
4. P3 list from `03-coverage.md` §5; then remove F-049 from `KNOWN_ISSUES` and make the `e2e`
   CI job required.
5. **N-014** FastAPI/Starlette upgrade (approved) with the backend and browser suites as nets.
6. Endpoint coverage from OpenAPI and background-job tests (not done in Phase 3).

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh`, create
`backend/.env` (RUNBOOK §3.1 + the block in `frontend/e2e/README.md`), `pip install` into
`backend/.venv` (Python 3.12), `npm ci` in `frontend`, then `frontend/e2e/scripts/start-stack.sh`
and `cd frontend && npx playwright test -c e2e` (about 50 minutes with 2 workers).

## Blockers
None for Phase 4. Open owner decisions: N-002, N-003, N-005, N-013, N-015, N-017, N-018, N-019
(viewer access, ERP posting by members), N-020 (missing capabilities), and the legal name in N-004.

## Budget notes
Phase 3 budget was about $30. It installed the stack twice (one container restart), ran the browser
suite in parts while writing it and once in full (about 50 minutes), and read only the parts of the
58k-line frontend it needed (inventory tools dumped page and form accessibility trees instead). The
exact spend is not visible from inside the session; check your usage page. If it went over, Phase 4
should start with items 1 and 2 only.

## Environment notes (for the next session)
- Branch pushes to `hardening/*` work from the cloud session (Phase 1 and 2 pushed there).
- **Two sessions worked on `hardening/security-deploy`.** A different Claude session
  (`session_01DDeTEB3q5Rz5XUnrsFMLw5`) pushed the F-019 and F-020 fixes to it at 08:37 to 08:39 UTC on
  2026-09-30. They were merged (never force-pushed) in `3b0aad1`; the three conflicts were comment and
  log-text only, and both sessions' tests for those fixes are kept and pass. Before any push, run
  `git fetch origin <branch>` and merge if the remote has moved.
- **Docker:** the `docker compose` CLI works (use `docker compose ... config` to render and check a
  compose file), but the daemon cannot pull images (Docker Hub rate limit), so nothing was run in
  containers. Do not spend time on `docker compose up` here.
- **Phase 3 scripts replace the manual steps below:** `frontend/e2e/scripts/start-db.sh` (native
  Postgres on /dev/shm port 5433 + Redis) and `start-stack.sh` (migrate, API, worker, mail sink).
  pgvector must still be built from source once per container (apt has 0.6.0; `apt-get update`
  first, then `postgresql-server-dev-16`, then `make && make install` in a v0.8.0 checkout).
- **The `verify_*.py` gates rewrite their evidence JSON** under `backend/evidence/` when run;
  `git checkout -- backend/evidence` afterwards (those files are off limits).
- **Postgres and Redis are native, not Docker:** PostgreSQL 16 with `pgvector` 0.8.0 built from source
  (the apt package is 0.6.0 and the app raises below 0.8), Redis 7.0, all on the RAM disk. Start a
  cluster with `setsid nohup /usr/lib/postgresql/16/bin/postgres -D /dev/shm/pgdata -p 5433 -c fsync=off
  -c synchronous_commit=off -c full_page_writes=off -c max_connections=200 -c listen_addresses=127.0.0.1
  -c unix_socket_directories=/tmp &` (as the `postgres` user, after `initdb`). Never use `pg_ctl restart`
  from the shell: it hangs the session. Clusters used: 5433 (main tree), 5434 (baseline/final worktrees),
  5435 (spare). `pg_isready` needs `-h localhost`.
- **Two pytest processes against one Postgres server corrupt each other** (the test database has a fixed
  name). Give the second one a different cluster, or set `TEST_DB_NAME`.
- Generate `backend/.env` from `.env.example` with random secrets (it is git-ignored); tests fail at
  start without `JWT_SECRET_KEY`.
- Use Python 3.12 (`backend/.venv`). Model hosts, Stripe, Groq and Dodo are blocked; set `ML_STUBS=true`.
- To compare the suite against the baseline: run it in a git worktree of `origin/main` and diff the
  sorted `FAILED`/`ERROR` ids with `comm -13`.
- `pkill -f <pattern>` can kill the calling shell when the pattern appears in the command line itself;
  use anchored `pgrep -f '^...'` and kill by PID.
- The Bash safety check occasionally returns "no verdict" (a transient service error). Retry once, then
  do file edits with the Edit and Write tools until it recovers.
