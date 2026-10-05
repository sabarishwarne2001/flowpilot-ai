# FlowPilot AI — browser tests (Playwright)

These tests drive the real web app in a headless Chromium browser against the
real API, worker, Postgres and Redis. They sign in as seeded users, click
through every page and module, and **fail on any page error, console error,
unhandled promise rejection or unexpected 4xx/5xx response**.

Results and the list of what they found: `docs/hardening/03-coverage.md`.

## What you need

- Everything from `docs/RUNBOOK.md` §3 (Postgres with pgvector, Redis, the
  backend virtualenv, `backend/.env`).
- Node 22 and `npm ci` in `frontend/`.
- Chromium for Playwright: `npx playwright install chromium` (once).

## `backend/.env` additions for the tests

Add these lines at the end (they only affect your local machine):

```
ML_STUBS=true                      # no model downloads; see RUNBOOK §7
STORAGE_BACKEND=local              # or keep minio if it is running
RATE_LIMIT_ENABLED=false           # every test signs in; the real limit is 10 per 5 min (F-048)
PLATFORM_SMTP_HOST=127.0.0.1       # the tests' own mail catcher (support/smtp-sink.mjs)
PLATFORM_SMTP_PORT=1025
PLATFORM_SMTP_USERNAME=e2e-relay   # any value: the catcher accepts every login
PLATFORM_SMTP_PASSWORD=e2e-relay-password
PLATFORM_SMTP_ENCRYPTION=NONE
```

## Run them

```bash
# 1. Database and Redis (Docker on your PC; the cloud sandbox uses start-db.sh)
cd backend && docker compose up -d db redis && cd ..
# 2. API + worker + mail catcher (migrates first; logs in /tmp/flowpilot-e2e-logs)
frontend/e2e/scripts/start-stack.sh
# 3. The tests (builds the production bundle, seeds, uploads sample documents)
cd frontend && npx playwright test -c e2e
# 4. The report, with a screenshot and a trace for every failure
npx playwright show-report e2e/playwright-report
```

On Windows run step 2 from Git Bash, or start the three processes by hand:
`uvicorn app.main:app --port 8000`, `python -m app.worker --loop all --profile all`
and `node frontend/e2e/support/smtp-sink.mjs`.

One file or one test: `npx playwright test -c e2e tests/30-auth.spec.ts -g "password reset"`.

## Switches

| Variable | Default | Meaning |
|---|---|---|
| `E2E_MODE` | `preview` | `preview` tests the production bundle (same-origin `/api`, as behind Caddy); `dev` tests the Vite dev server |
| `E2E_WORKERS` | `2` | parallel browsers |
| `E2E_LLM` | unset | set to `1` when the API has a working LLM key; the assistant answer tests are skipped otherwise |
| `E2E_STRICT_KNOWN` | unset | `1` also fails tests on known issues (see below) |
| `E2E_API_ORIGIN` | `http://127.0.0.1:8000` | where the API runs |
| `E2E_PYTHON` | `backend/.venv` python | the Python that runs the seed |

## How it is built

- `global-setup.ts` checks the API, runs `backend/scripts/seed_e2e.py` (idempotent:
  every run converges on the same users, plans and roles) and uploads the sample
  documents through the real upload API, then waits for processing.
- Seeded accounts (password in `support/env.ts`, development/test only):
  Tenant A *DevCo Labs* (Developer: owner, admin, billing, member, viewer),
  Tenant B *BizCo Holdings* (Business: owner), Tenant C *Caretakers Global Inc*
  (Enterprise: owner, admin, member, viewer; workspaces Operations and Finance),
  and a platform super-admin.
- `support/fixtures.ts` is the strict fixture. Each test gets a **fresh** session
  (refresh tokens rotate and reuse ends the session, so sessions are never shared).
  A test that expects an error declares it: `problems.allowHttp(/pattern/, [402], "why")`.
- `KNOWN_ISSUES` in the fixture lists defects that would otherwise fail every
  test (today only F-049, the avatar 404). They are still reported as
  annotations, and `tests/known-issues.spec.ts` turns red when one is fixed, so
  the entry gets removed.
- `support/sample-docs.ts` builds the invoices, PO, goods receipt, contract and
  4-page packet as real PDFs with a text layer (byte-identical on every run).
- `tools/` holds two inventory scripts used to write the tests (page and form
  accessibility trees); they are not part of the suite.
