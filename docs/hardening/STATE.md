# Hardening campaign — STATE

_Last updated: 2026-10-06 (end of Phase 5)_

## Current phase
**Phase 5 — Production readiness: every open finding worked. COMPLETE; stopped at the Phase 5
checkpoint.** Branch `claude/blissful-goldberg-hsqxy1` (the only branch this cloud session may push
to; per N-001 the PR title names the intended `hardening/phase-5-production-readiness`).
PR: PH_PR_LINK. Full report: `05-production-readiness.md`.
Do not redo Phases 0 to 5. The next session starts after you have read the PR.

## What is done
- **Phases 0 to 4:** see `00-map.md` … `04-core-engines.md` (PRs #1 to #5, merged).
- **Phase 5** (this branch; the PR lists every commit):
  - Owner decisions **N-021 to N-025 applied** (BYOK Business + Enterprise on the server and in the
    console; lockout, avatar, token-link and ARCH-05 decisions turned into the tests).
  - **Every open finding worked**: F-005, F-008, F-009, F-010, F-014, F-015, F-016, F-017 (22 real
    differences), F-025 to F-028, F-033 (closed), F-039 (12 of 13 packages), F-045 (OCR split),
    F-048, F-049, F-053 to F-060, F-062, F-064, F-095 to F-099 fixed or closed; eight new defects
    found and fixed (F-100 to F-107), among them **lost sign-up/reset/invitation emails when a
    connection closed early (F-106, P1)** and **invitees landing on "no longer available" (F-107)**.
  - **Dependencies:** FastAPI 0.136.3 + Starlette 1.7.0 (N-014), PyJWT instead of python-jose, pypdf,
    urllib3, cryptography and four more. pip-audit: 44 advisories in 13 packages → 5 in 3.
  - **Migration** `p5a1_schema_drift_alignment` (one head). Drift baseline 314 → 283 lines.
  - Full backend suite: **3,223 passed, 0 failed**, 9 skipped (`main`: 3,134 passed, 22 failed). Gates: none regressed (37/35/6 → 38/34/6).
  - Browser suite: **271 passed, 8 failed, 2 skipped** (Phase 3: 233 / 40). The 8 are 4 features you have not chosen yet (N-020) and 4 that need real model output (F-063).
  - `COVERAGE.csv` (1,322 rows): untested 674 → **418**, smoke 401 → **634**, deep 241 → **270**; endpoints
    untested 258 → **34** (a new recorder maps which routes the passing backend tests reach).

## Owner decisions in force (do not re-ask)
- **N-004** proprietary, all rights reserved ("FlowPilot AI" until the legal name is given).
- **N-006** OWNER and ADMIN may both create API keys (sidebar now shows API keys to ADMIN).
- **N-007** production is a Linux VPS running Docker Compose.
- **N-008** keep the pinned community image `pgsty/minio` (which includes `mc`).
- **N-009** pre-launch: the first production deploy may set `ARCH40_CONTRACT=1` once.
- **N-010** the campaign MAY READ failing `verify_*.py` gates but must NEVER edit, weaken, delete or
  skip one. `apply_*.py`, `backend/evidence/`, `arch07_*`, `arch08_*`, PDFs and certification files
  stay off limits.
- **N-011** plans and prices as in `seed_quota_tiers.py` (BYOK added to Business and Enterprise by N-021).
- **N-012** a 24-hour recovery point is accepted for launch.
- **N-014** framework upgrade approved (done in Phase 5).
- **N-019 (ERP part)** ERP posting is workspace ADMIN / organization OWNER-ADMIN only.
- **N-020 items 1, 4, 6** built (Phase 4).
- **N-013** no longer needed: Dodo's webhook has no mode field (F-033 closed).
- **N-021** BYOK is Business and Enterprise. **N-022** locked-out sign-in keeps the generic 401.
  **N-023** refuse > 50 MP, shrink the rest to 1024 px. **N-024** keep the public token URLs.
  **N-025** the ARCH-05 lock check covers organization/member rows only.

## Next action (exact)
1. Owner: read the PR and `05-production-readiness.md`; answer the still-open decisions below.
2. On a machine with Docker (your PC or the VPS): build the four images
   (`docker build --target web|worker|enrich|ocr backend`), run `docker compose -f
   docker-compose.prod.yml --env-file .env.production config`, and follow RUNBOOK "First deploy".
   That is the only way to verify F-006/F-040/F-024. The images now install `requirements-web.txt`
   (and the `ocr` image adds `requirements-ocr.txt`; F-045): check the image sizes there, and on a
   machine that can reach download.pytorch.org try the CPU-only torch build (−3.7 GB per image),
   together with the torch 2.13 upgrade.
3. Run the browser suite once with a real LLM key (`E2E_LLM=1`) to close F-063.
4. After the first real deploy, watch the browser console for CSP reports (N-015).

How to bring the test stack up in a fresh sandbox: `frontend/e2e/scripts/start-db.sh` (needs
pgvector 0.8.0 built from source: `apt-get install postgresql-server-dev-16`, then `make && make
install` in a v0.8.0 checkout), create `backend/.env` from `.env.example` plus the block in
`frontend/e2e/README.md` with random secrets, `python3.12 -m venv backend/.venv` and `pip install -r
requirements.txt -r requirements-dev.txt` (about 7 GB, 15 minutes), `npm ci` in `frontend`.

## Blockers
None in engineering. Open owner decisions: **N-002** (plan gating of marketplace, service levels, data
governance), **N-003** (downgrade policy; the code follows read + delete), **N-005** (history rewrite),
**N-015** (CSP enforcement after a real session),
**N-016 (a)/(b)** (retire historical scripts), **N-017** (extras before launch), **N-018** (sign-in
allowance: now one `.env` line; option (c) needs code), **N-019 viewers part** (read access or hide),
**N-020 items 2, 3, 5, 7**, and the legal name in **N-004**.

## Budget notes
Phase 5 ran in one session: environment set-up (venv, pgvector, two RAM-disk Postgres clusters), a
full baseline suite on `main` (23.6 min), the fixes, the full suite on the branch and the browser
suite (run together, so both were slower). The exact spend is not visible from inside the session;
check your usage page.

## Environment notes (for the next session)
- **After changing `backend/requirements.txt`, run `python scripts/image_requirements.py`** (in the
  backend venv): it regenerates `requirements-web.txt` / `requirements-ocr.txt`, and
  `tests/infra/test_image_requirements.py` fails until you do.
- This session pushed to `claude/blissful-goldberg-hsqxy1` (harness-assigned). Earlier phases pushed
  to `hardening/*`; both work. Before any push, `git fetch origin <branch>` and merge if it moved.
- **FastAPI is pinned below 0.137 on purpose.** From 0.137 included routers stay nested
  (`_IncludedRouter`) in `app.routes`, which empties the route table that `assert_public_route_registry`
  and every route-sweep security test walk. Upgrading past it needs those walkers ported to
  `fastapi.routing.iter_route_contexts` first.
- **Logging now works after in-process migrations (F-100):** the test output shows app warnings and
  errors that were silent before, for example "SLO shutdown flush …" (fixed, F-102). Do not "fix" a
  noisy log by disabling loggers again.
- **Two pytest processes against one Postgres server corrupt each other** (fixed database names).
  Use a second cluster (`E2E_PGDATA=/dev/shm/pgdata_base E2E_PGPORT=5434 frontend/e2e/scripts/start-db.sh`)
  and `POSTGRES_PORT=5434 REDIS_URL=redis://localhost:6379/5 pytest …`.
- To compare the suite against `main`: `git worktree add <dir> origin/main`, copy `backend/.env`, run
  the same command there on the second cluster, and diff the sorted `FAILED`/`ERROR` ids.
- Docker: the CLI renders compose files (`docker compose ... config`) but the daemon cannot pull
  images; nothing was run in containers.
- Model hosts, Stripe, Groq and Dodo are blocked; `ML_STUBS=true`. PyPI and npm are reachable.
- `pkill -f <pattern>` can kill the calling shell; use anchored `pgrep -f '^...'` and kill by PID.
