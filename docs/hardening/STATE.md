# Hardening campaign — STATE

_Last updated: 2026-09-30 (end of Phase 2)_

## Current phase
**Phase 2 — Deployment blockers and security hardening: COMPLETE. Stopped at the
Phase 2 checkpoint.** PR: https://github.com/sabarishwarne2001/flowpilot-ai/pull/3 (branch `hardening/security-deploy`). The next
session starts Phase 3 after you have read the PR. Do not redo Phases 0 to 2.

## What is done
- **Phase 0:** `00-map.md`, `COVERAGE.csv`, `FINDINGS.md`, `NEEDS-OWNER.md` (PR #1, merged).
- **Phase 1:** boot and honest baseline (PR #2, merged): CI runs, a fresh clone installs,
  migrates, tests and starts; 2,285 passed / 219 failed / 33 errors (F-016); 39 of 78 gates pass.
- **Phase 2** (this branch; the full report is `02-security-deploy.md`):
  - P1 bugs fixed: F-020 (bbox), F-019 (fake OCR), F-021 (echoed secrets), F-040 (no `gunicorn`),
    F-047 (23 production settings never reached the containers).
  - Production refuses to start on unsafe config; public default secrets removed (F-003).
  - Tenant isolation proven over all 423 tenant routes; IDOR, roles, super-admin and partner
    routes proven; two soft-IDOR routes and five plan-gating holes closed (F-031, F-004).
  - Upload path hardened (F-035, F-036, F-037); SSRF, SQL injection and automation injection
    checked and pinned; billing webhook signatures, replay and secret rotation proven.
  - Sign-in rate limit proven against real Redis (F-048 recorded).
  - Deployment for a Docker Compose VPS: backups with a restore drill, container job scheduler,
    heartbeats, readiness probe, pinned images, no source maps, no public API docs, LICENSE,
    `docs/RUNBOOK.md` section 9 (F-006, F-024, F-038, F-041 to F-044, F-046, F-007).
  - Secrets scan of all 113 commits: no real credential found (limits in FINDINGS).
  - 494 new tests in 35 files (11 from the parallel session); no existing assertion changed.
    Full serial run: 2,769 passed, 214 failed, 33 errors; nothing red that was not already red in
    Phase 1, and 5 tests that were red now pass. CI runs the Phase 2 proofs in a step of their own.
  - `COVERAGE.csv`: 26 rows now `deep` and 32 more `smoke` (production settings, the upload and
    billing-webhook endpoints, the readiness probe); everything else is still `untested`.
  - `FINDINGS.md` now holds F-001 to F-048; `NEEDS-OWNER.md` holds N-001 to N-018.

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
Phase 3, per THE_MASTER_PROMPT. From the Phase 2 residual risks, in this order:
1. **N-014** FastAPI/Starlette upgrade (13 advisories, including the upload parser) and the
   python-jose to PyJWT swap, with the whole suite as the safety net. Needs the owner's go-ahead.
2. **Billing lifecycle proof** with recorded gateway payloads: failed payment, dunning,
   cancellation, downgrade, out-of-order events, quota enforcement (needs N-013 for Dodo).
3. **Triage the ~215 red tests** (F-016) and the 33 failing gates (F-029, N-010 allows reading
   the failing gates); fix or get the owner to retire, never weaken.
4. Object-level isolation for the remaining collections; tenant-edited email and branding
   template injection; prompt injection through the assistant with a recorded model.
5. Watch the first real deploy with the owner (RUNBOOK 9.2) and fix what it teaches.
6. Phase 4 candidates already logged: F-045 (split the images), F-015 (sidebar vs API roles),
   F-005, F-008 to F-010, F-014, F-028, the Redis password, digest pinning.

Before starting: read `docs/RUNBOOK.md` sections 3 and 5 and `02-security-deploy.md` section 5.

## Blockers
None for Phase 3 (`BLOCKERS.md`: no fix needed three attempts). Open owner decisions:
N-002, N-003, N-005, N-009 (production answer), N-011 to N-018, and the legal name in N-004.
N-014 blocks only the framework upgrade; N-013 blocks only the Dodo mode-guard fix.

## Budget notes
Phase 2 budget was about $15. Phase 2 ran the whole suite several times (about 25 minutes each),
built PostgreSQL extension `pgvector` from source, ran the gates twice, scanned the git history and
wrote 494 tests. The exact spend is not visible from inside the session; check your usage page.
If it went over, Phase 3 should start with item 1 or 3 only.

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
