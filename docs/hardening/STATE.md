# Hardening campaign — STATE

_Last updated: 2026-10-06 (final commercial release)_

## Current phase
**Final release — COMPLETE. Verdict: GO for production deployment** (`05-release-readiness.md`).
Branch `hardening/final-commercial-release` (mirrored to the session branch
`claude/vigilant-heisenberg-ytagy4`). PR: {{PR_URL}}.
Run under founder authority: every open owner decision was taken and is recorded in
NEEDS-OWNER.md ("Final release"). The campaign's engineering work is done; what remains is the
first deployment (RUNBOOK §9) and the post-launch list in `05-release-readiness.md` §6.

## What is done
- **Phases 0 to 5:** `00-map.md` … `05-production-readiness.md` (PRs #1 to #6).
- **Final release** (this branch; the PR lists every commit):
  - **Stage 1 — decisions and missing features.** N-002 to N-020 decided and applied:
    notification filters and mark as unread, promo code at checkout, invite from Organization →
    Members, page image beside the fields with in-place correction, Service levels targets on
    Enterprise, viewer/admin sidebar rules, sign-in allowance 20/5 min, Redis password, uptime
    heartbeat, CSP enforced, two-factor sign-in.
  - **Stage 2 — live end-to-end pass** over all 27 module trees with a deterministic local model
    stand-in (F-063 closed) and the production CSP enforced. New defects F-108 to F-122 found and
    fixed (two P1: legal hold bypass F-108, first document skipping AI F-113).
  - **Stage 3 — repository.** 286 historical files moved unchanged to `archive/`; CI gates job
    retired; README and RUNBOOK updated.
  - **Stage 4 — certification.** OWASP ASVS 4.0.3 Level 2 and Twelve-Factor reviews; the ASVS
    audit found and fixed four authentication gaps (F-117 to F-120: password policy and strength
    meter, show-password, session lifetime, sign-out on a typo), API caching (F-122), and added a
    Permissions-Policy.
  - Backend suite: **3,298 passed, 0 failed, 9 skipped**. Browser suite: **290 passed, 0 failed, 1 skipped** (by design) (start of release:
    272 passed, 7 failed, 2 skipped). One Alembic head (`p6a3_user_mfa_factors`), 0 new drift.
  - `COVERAGE.csv` (1,345 rows): deep 270 → **305**, smoke 634 → 622, untested **418** (unchanged;
    endpoints untested 34).

## Owner decisions in force (do not re-ask)
All of Phase 5's (N-004, N-006 to N-014, N-019 ERP part, N-020 items 1/4/6, N-021 to N-025) plus,
from this release: **N-002** service-level targets Enterprise; **N-003** read + delete after a
downgrade; **N-005** no history rewrite; **N-015** CSP enforced; **N-016** scripts archived;
**N-017** MFA, Redis password, uptime heartbeat, image pinning; **N-018** 20 sign-ins per address
per 5 minutes; **N-019** viewers do not see Workflows / Run history / Review queue; **N-020** all
seven capabilities built. Details: NEEDS-OWNER.md, "Final release".

## Next action (exact)
1. Owner: review and merge the PR.
2. On the VPS, follow `docs/RUNBOOK.md` §9 "First deploy": build the four images, fill
   `.env.production` from the template (new this release: `REDIS_PASSWORD`; optional
   `LOG_FORMAT=json`, `SESSION_*` for other session limits), set `HEARTBEAT_UUID_UPTIME` in the
   installed cron file (`deploy/cron.d`, RUNBOOK 9.6), `docker compose ... config`,
   migrate, start, run the smoke checks, open the browser console once (CSP check), pin the image
   digests (step 11).
3. Turn on two-factor sign-in for your own account (Settings → Profile).
4. Post-launch list: `05-release-readiness.md` §6.

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
One session: environment set-up, the Stage 1 features, three full browser runs and three full
backend runs (on two RAM-disk Postgres clusters in parallel), the fixes, and the documentation. The
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
