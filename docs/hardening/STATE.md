# Hardening campaign — STATE

_Last updated: 2026-09-30 (end of Phase 0)_

## Current phase
**Phase 0 — Map and ledger: COMPLETE. Stopped at the Phase 0 checkpoint.**
The next session starts Phase 1. Do not redo Phase 0. If the code changes a
lot, re-run `docs/hardening/tools/regen_ledger.sh` (read its warning first).

## What is done
- `COVERAGE.csv`: 1,306 rows, all `untested`. That is 82 pages, 49 nav
  items, 55 seed UI actions, 558 endpoints, 73 background jobs (49 job types,
  7 worker loops, 17 host-cron entries), 151 migrations, 306 config variables
  and 32 integrations. 405 rows are tagged `[CRITICAL:<journey>]`.
- `00-map.md`: architecture, plan-to-nav-lock table, nav cross-check flags,
  numbers, top-15 risks, repo hygiene, how the ledger was built.
- `FINDINGS.md`: F-001..F-015 (2 P1, 3 P2, 10 P3). All come from reading code
  or git and GitHub metadata. None is fixed.
- `NEEDS-OWNER.md`: N-001..N-007.
- `tools/`: the scripts that regenerate the ledger (`regen_ledger.sh`).

## Next action (exact)
Phase 1 on branch `hardening/baseline` (see N-001 about branch names):
1. F-001 first: find why GitHub rejects `.github/workflows/ci.yml` (0 jobs
   per run). The error text is on the run page, e.g.
   https://github.com/sabarishwarne2001/flowpilot-ai/actions/runs/36674718556
   Then run `python3 backend/scripts/normalize_encodings.py --apply` (44 files
   fail the `encoding` gate today) as its own commit.
2. Bring up Postgres 16 + pgvector, Redis and MinIO. Docker 29.3.1 is present
   in this container, and native `psql` and `redis-server` also exist.
   Try `docker compose -f backend/docker-compose.yml up -d db redis minio` first.
3. Install backend deps. `backend/requirements.txt` is UTF-16 and pulls torch,
   paddlepaddle, paddleocr and sentence-transformers (several GB). If they
   cannot be installed or downloaded, add a clearly labelled test stub and say so.
4. `alembic upgrade head` on an empty DB → `downgrade -1` → `upgrade head`.
   Require 1 head and no autogenerate drift.
5. Start the API and worker, check `/api/v1/health` and `/api/v1/openapi.json`,
   and compare the OpenAPI path list with the 555 statically extracted
   endpoints.
6. Run the full `pytest -q --maxfail=5` (CI's command),
   `scripts/run_all_gates.py --static-only`, frontend `npm ci`, `tsc`,
   `lint` and `build`. Upgrade ledger rows only where a test passed.
7. Write `01-baseline.md`, `docs/RUNBOOK.md` and `README.md`. Say whether
   Playwright can run here (Chromium is preinstalled at `/opt/pw-browsers`).

## Blockers
None for Phase 1. Open owner decisions: N-001..N-007 (none block Phase 1).

## Budget notes
Phase 0 budget was about $8. Phase 0 was done in one session using static
analysis only: no dependencies installed, no services started. The exact
spend is not visible from inside the session. Check your usage page, and
tell the next session if the Phase 1 budget ($15) should change.

## Environment notes (for the next session)
- The cloud session was assigned branch `claude/new-session-uaku80`, and Phase
  0 was pushed there (only `docs/hardening/` changed). See N-001.
- Python 3.11.15 is available. Backend dependencies are NOT installed.
- The GitHub MCP tools work (Actions run listing used for F-001).
