# Hardening campaign — STATE

_Last updated: 2026-10-05 (end of Phase 4)_

## Current phase
**Phase 4 — Live engine hardening and core feature verification: COMPLETE. Stopped at the Phase 4
checkpoint.** Branch `hardening/phase-4-core-engines`; full report `04-core-engines.md`.
The next session starts after you have read the PR and answered N-021 to N-025 (and, if you want
them, N-019 viewers part and N-020 items 2/3/5/7). Do not redo Phases 0 to 4.

## What is done
- **Phases 0 to 3:** see `00-map.md` … `03-coverage.md` (PRs #1 to #4, merged).
- **Phase 4** (this branch):
  - A live engine harness (`backend/tests/engines/`, 30 files): real uploads, the real worker,
    the database and the API, with a recorded model; every core engine and governance feature
    driven end to end with every role; a workspace-isolation sweep over the live route table for
    23 features. CI step "Phase 4 engine proofs".
  - **26 defects fixed** with red-then-green proof (F-051, F-052, F-065, F-066, F-067 to F-071,
    F-073 to F-079, F-081 to F-090), among them F-051 (P1 invite crash), F-078 (request hang that
    locked the organization), F-079 (SLOs never recorded), F-081 (invoice correction + billing job
    crash), F-084 (automation carried on after a security violation), F-088 (image-bomb avatar).
  - **Built:** global search across workspaces (F-091), webhook "send test event" (F-080), page
    evidence in the review workbench (F-092), payment-risk flags on the radar (F-093), extraction
    memory fills empty fields (F-072). Migrations `p4a1`, `p4a2`, `p4a3`; one head.
  - About 230 backend tests that were red on `main` for stale reasons were aligned (no assertion
    weakened; every plan gate they now pass has an explicit refusal test) (F-094).
  - Full backend suite: `main` 2795 passed / 204 failed / 33 errors → this branch **3132 passed /
    21 failed / 0 errors**; no new failure; the 21 are listed in F-099 and N-021 to N-025.
  - `COVERAGE.csv`: 241 deep, 401 smoke, 674 untested (was 55 / 287 / 967); `deep` means an engine
    test got a successful answer from that route (tools/coverage_from_engines.py).

## Owner decisions in force (do not re-ask)
- **N-004** proprietary, all rights reserved ("FlowPilot AI" until the legal name is given).
- **N-006** OWNER and ADMIN may both create API keys.
- **N-007** production is a Linux VPS running Docker Compose.
- **N-008** keep the pinned community image `pgsty/minio` (which includes `mc`).
- **N-009** `ARCH40_CONTRACT=1` for dev, test and staging. Production is still open.
- **N-010** the campaign MAY READ failing `verify_*.py` gates to diagnose them but must NEVER edit,
  weaken, delete or skip a gate script. No gate may be retired without the owner. `apply_*.py`,
  `backend/evidence/`, `arch07_*`, `arch08_*`, PDFs and certification files stay off limits.
- **N-019 (ERP part)** posting to the ERP is restricted to workspace ADMIN / organization
  OWNER-ADMIN (Phase 4 brief; done, F-065).
- **N-020 items 1, 4, 6** build them (Phase 4 brief; done).

## Next action (exact)
1. Owner: read the PR and `04-core-engines.md`; answer N-021 to N-025.
2. Engineering, in order: F-099 items that need no decision (storage-boundary allowlist with
   reasons, the OCR import test's isolation), then F-053 once N-019 (viewers) is answered.
3. Phase 3 leftovers not in the Phase 4 brief: F-054 to F-064; make the browser `e2e` CI job
   required once green.
4. N-014 FastAPI/Starlette upgrade (approved) with the backend and browser suites as nets.

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh`, create
`backend/.env` (RUNBOOK §3.1 + the block in `frontend/e2e/README.md`), `pip install` into
`backend/.venv` (Python 3.12), `npm ci` in `frontend`. Engine proofs: `cd backend && pytest -q
tests/engines` (about 10 minutes). Full backend suite: about 90 minutes here.

## Blockers
None. Open owner decisions: N-002, N-003, N-005, N-013, N-015, N-017, N-018, N-019 (viewers part),
N-020 (items 2, 3, 5, 7), N-021 to N-025, and the legal name in N-004.

## Budget notes
Phase 4 ran in one long session with one usage-limit pause and two container restarts (Postgres and
Redis restarted with `start-db.sh`). The exact spend is not visible from inside the session.

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
