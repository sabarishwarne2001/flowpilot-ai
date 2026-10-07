# Hardening campaign — STATE

_Last updated: 2026-10-07 (production configuration & UI elevation)_

## Current phase
**Production configuration & UI elevation — COMPLETE, in review.** Branch
`hardening/production-config-and-ui-elevation` (session branch `claude/sweet-bell-ahiem4`); PR: to be opened from this branch.
The final release (PR #7, GO) is merged into `main`; this branch builds on it.

- **Track 1 (configuration).** Every key the app, the compose files and the scripts read was checked
  against the four env files. Fixed F-123 (SSO advertised `http://localhost:8000` on every deployment;
  the settings that should fix it were ignored). Templates aligned (R2 = `STORAGE_BACKEND=r2`, model
  names and `PUBLIC_API_URL` passed to the containers, Vite port, reranker token, Enterprise price id).
  The owner's real `.env` / `.env.production` were finalized outside the repository and the production
  one was verified with `docker compose config` plus the app's start-up guard: its only refusal is the
  Stripe webhook secret (F-125). New owner items N-026 to N-029.
- **Track 2 (UI).** Design tokens (layered dark zinc, cool light), bundled Inter/JetBrains Mono,
  tactile shared primitives; split-screen auth (sign-in with steps, sign-up, reset, verification,
  invitations) with a product showcase; Linear-style shell (collapsible sidebar, portal workspace
  switcher, profile menu, glass top bar with Ctrl+K); page polish (type weights, unified cards and
  buttons, Overview, Documents, the document workbench, settings, dialogs, palette, selects).
- Verification in progress: browser suite and backend suite on the final build (see the PR).

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
1. Owner: review and merge the PR (screenshots in it).
2. Owner decisions N-026 (gateway and the three plan price ids), N-027 (email provider), N-028 (domain
   and admin mailbox), N-029 (sign-in page claims).
3. Stripe Dashboard (test mode): add the webhook endpoint
   `https://app.flowpilot.ai/api/v1/billing/webhooks/stripe` and put its signing secret in
   `.env.production` (F-125); the app refuses to start until then.
4. Test the Gemini `AQ.` key from your own machine (CHANGES note); replace it with an AI Studio key if
   it is refused.
5. Before the first deploy, regenerate the server secrets on the server and roll the R2 token, Gmail
   app password and Groq keys (they were pasted into a chat).
6. Then the first deploy as before: `docs/RUNBOOK.md` §9 (sweepers.env now carries
   `HEARTBEAT_UUID_UPTIME`).
7. Before the first customer configures SCIM: F-124 (SCIM token pepper).

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
