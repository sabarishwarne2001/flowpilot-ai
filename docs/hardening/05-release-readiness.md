# FlowPilot AI — Release-readiness certification (Phase 3, final commercial hardening)

_Date: 2026-10-08 · Branch: `hardening/phase-3-final-commercial-hardening` · Head migration:
`q1a1_retired_groq_models`. Supersedes the final-release certification of 2026-10-06 (kept in git
history)._

## Verdict

**<<VERDICT>>**

<<VERDICT_TEXT>>

What this verdict does **not** cover, because it cannot be done from this environment, is the
deployment itself: building the images and starting the production stack on the VPS (no Docker
daemon here, F-006). That is the first-deploy procedure in `docs/RUNBOOK.md` §9; its smoke checks are
part of deploying, not a condition on the code. Before the first paying customer: Stripe test-mode
prices and webhook secret (F-125), the Postmark server (N-027).

---

## 1. How this was certified

The full stack ran as it runs in production (Postgres 16 + pgvector, Redis, the API, the real
worker loop, the web app; a deterministic local model stand-in instead of a paid provider) and was
driven through every page as eleven seeded people: the four plans (Free, Developer, Business,
Enterprise) and every organization role (Owner, Admin, Billing, Member) and workspace role (Admin,
Contributor, Viewer), plus the platform super-admin. The API and worker logs were watched the whole
time for tracebacks, ERROR lines and 5xx; none appeared except around deliberate restarts. A crawler
clicked every control each page offers (414 actions for the Enterprise owner alone: zero console
errors, zero failed requests) and recorded every refused request per role; every page was loaded at
390 px (phone) and 768 px (tablet). All 13 application sweeps were run by hand with their cron
arguments; 225 background jobs of 35 types ran to SUCCEEDED, none failed or dead. Ownership transfer
was run end to end between two people. Every defect found was first reproduced by a failing test,
then fixed, then proven by that test and the full suites (the Evidence Rule).

## 2. What Phase 3 changed

**Fixed (FINDINGS.md F-182 to F-205)** — five P1s: the nightly compliance sweep never ran (F-182) and
the retention purge left files in storage (F-183); requiring SSO with no identity provider locked the
organization out (F-188); a failed request was re-asked ~30 times a second (F-195); every new
workspace was set to an AI model Groq had retired, so its first document would have failed in
production (F-196). Nine P2s, among them an organization's maximum session age shown but never
applied (F-204), unsigned SAML logout requests accepted (F-205), the job SLOs never measured
(F-189), BYOK and margin reports counting the self-hosted model as customer keys (F-190, F-202), the
Billing role unable to read usage (F-192), spend limits vanishing from the page (F-193), tolerance
versions resetting to zero (F-199) and RevOps actions sent on Cancel (F-186). Ten P3s of role
truthfulness, wording and layout (F-184, F-185, F-187, F-191, F-194, F-197, F-198, F-200, F-201,
F-203).

**Elevated** — one console header and URL-synced keyboard tabs across the organization consoles,
identity, billing, marketplace, autonomy, audit and the platform admin consoles; billing in the
customer's words; service levels described in plain language; sentence case and labelled fields on
the remaining settings and auth screens; the organization session limit as a real owner control.

## 3. Evidence

| Check | Start of Phase 3 | Final | Command |
|---|---|---|---|
| Backend test suite | 3,479 passed, 0 failed, 9 skipped | **3,527 passed, 0 failed, 9 skipped** (48 min; 48 more tests than Phase 2) | `pytest -q` (backend) |
| Browser suite (Playwright, Chromium; fresh database, production preview, CSP, model stand-in) | 360 passed, 0 failed, 1 skipped | **<<BROWSER>>** | `E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e` |
| API / worker logs during the browser run | clean | **<<LOGS>>** | tracebacks, ERROR, 5xx, failed or dead jobs |
| Alembic heads | 1 (`p9a1`) | **1** (`q1a1_retired_groq_models`); up, down, up checked | `alembic heads` |
| Model/migration drift | 283 known, 0 new | **283 known, 0 new** | `scripts/check_migration_drift.py` |
| Frontend type-check | clean | **clean** (`tsc -b`, `tsc --noEmit`) | `npx tsc -b` |
| Frontend lint | clean | **0 errors, 0 warnings** | `npm run lint` |
| Frontend self-checks | clean | **clean** (tenant resolution, paths, reconciliation, permission parity) | `npm run check:self` |
| Frontend production build | builds | **builds; no source maps** | `npm run build`, `check-no-sourcemaps.mjs --dist` |
| npm advisories (production deps) | 0 | **0** | `npm audit --omit=dev` |
| Python advisories | 5 in 3 packages, accepted | **5 in 3 packages, accepted** (§6) | `pip-audit -r requirements.txt` |
| Text encoding gate | clean | **clean** (2,247 files) | `scripts/normalize_encodings.py --check` |
| Coverage ledger | 380 deep | **408 deep** of 1,400 rows | `COVERAGE.csv` |

The browser suite's one skip is by design: "the AI provider is down" runs only when no model is
attached. Model-dependent flows run against `frontend/e2e/support/llm-mock.mjs`, a deterministic
OpenAI-compatible stand-in, in every run and in CI (F-063).

## 3a. Plan × role matrix

Checked live and by `02-plan-matrix`, `03-role-matrix` and `26-phase3-consoles`: each plan sees the
pages its plan includes and a View plans screen (server: 402) for the rest; each organization role
(Owner, Admin, Billing, Member) and workspace role (Admin, Contributor, Viewer) sees only actions the
server allows it, and an address opened without the role shows Access restricted before any request
is made (F-194). The platform admin API answers 404 to every tenant role, owners included (it does not admit it exists; checked live for the Enterprise owner), and the `/admin` pages send them back to their workspaces (route walkers in
`tests/security/` cover every admin route, including the new `GET /admin/revops/organizations`).

## 4. OWASP ASVS 4.0.3 — Level 2

Status per chapter. "Pass" means the controls the chapter asks for at Level 2 are in place and
tested; deviations are listed with the reason they are accepted.

| Chapter | Status | Evidence |
|---|---|---|
| **V1 Architecture** | Pass | Tenancy model, trust boundaries and data flows in `00-map.md`, `02-security-deploy.md`; one public-route registry (`app/core/public_route_registry.py`) that every unauthenticated route must be listed in, enforced at start-up. |
| **V2 Authentication** | Pass | Passwords: 12+ characters, checked against breached and common passwords with zxcvbn, no composition rules, strength meter, show-password (F-119, V2.1.1–2.1.12). Argon2id at the OWASP floor (19 MiB, t=2). Generic sign-in errors; per-address limit, per-(address, account) refusal ladder and per-account slow-down (V2.2.1). Two-factor sign-in, TOTP RFC 6238 with replay protection, recovery codes hashed, wrong codes capped (V2.8). Reset tokens single use, short-lived, never in a query string (V2.5). |
| **V3 Session management** | Pass | Opaque refresh tokens (hashed at rest) rotated on every use with reuse detection and family revocation; 10-minute access tokens with a `type` claim; HttpOnly, Secure, SameSite=Lax cookie scoped to the auth path (V3.4). Sign-out, sign-out everywhere, device list with revoke; password change revokes every other session (V3.3.1, V3.3.3). Re-authentication 12 hours after sign-in or after 30 idle minutes (F-120, V3.3.2), and sooner when an organization the person belongs to sets a shorter maximum session age (F-204, validated, enforced at refresh). Step-up re-authentication for the billing portal. SAML single logout accepts only requests signed by the session's identity provider (F-205). |
| **V4 Access control** | Pass | Every route authorizes on the server: role matrix, cross-tenant, IDOR-on-child-entity and super-admin-only sweeps generated from the OpenAPI schema, so a new route is tested the day it is added (`tests/security/`). Plan gating server-side (402 `CAPABILITY_REQUIRED`). Tenant isolation suite. Phase 3: the interface offers each action only to the roles the server allows (F-192 to F-201), so no screen invites a refused request. |
| **V5 Validation, sanitization, encoding** | Pass | Pydantic schemas on every body; injection sweep and NUL-byte guard over every route; validation errors never echo input; SQL only through the ORM or bound parameters; outbound requests through an SSRF-checked client (`test_ssrf_client.py`); React escaping plus DOMPurify for the one rendered-Markdown view; CSP enforced as the second line (V5.3). |
| **V6 Stored cryptography** | Pass | Secrets at rest (SMTP passwords, BYOK keys, TOTP secrets) encrypted with MultiFernet and rotatable keys; API keys and recovery codes stored as HMAC-SHA256 with a pepper; tokens from `secrets`; the JWT secret is required and checked against known-leaked values; production refuses to start with weak or default secrets (`production_guard.py`). |
| **V7 Errors and logging** | Pass | One error envelope `{code, message}` with no stack traces; every sign-in, refusal and second-factor challenge logged with the client address and, once known, the account id (never the password, code or token); an audit log per organization of security-relevant changes with actor, outcome and IP (two-factor on/off and recovery-code use, roles, plans, keys, deletes, field corrections); log lines carry context with secret-looking keys masked and token paths redacted in the access log (F-114). |
| **V8 Data protection** | Pass | Legal holds stop every destroying path (F-108); retention and GDPR erasure with an audit trail, the nightly retention purge scheduled with working arguments and releasing the stored files (F-182, F-183); API responses `Cache-Control: no-store` unless a handler asks for caching (F-122, V8.2.1); no source maps shipped; `index.html` not cached. |
| **V9 Communications** | Pass | TLS everywhere through Caddy (automatic certificates, HSTS with preload, HTTP redirected); internal traffic stays on the private Docker network. |
| **V10 Malicious code** | Pass | Locked dependency versions (`requirements.txt`, `package-lock.json`); no third-party script or CDN in the page (CSP `script-src 'self'`); `npm audit` clean; pip-audit in CI; secret scanning of the history (`scan_history_for_secrets.py`). |
| **V11 Business logic** | Pass | Rate limits per route family with standard headers; a failed request is never re-asked in a loop by the client (F-195); idempotency keys on uploads and bulk actions; spend limits on AI usage; invitation resend cooldown; calibrated autonomy holds risky automation for review. |
| **V12 Files and resources** | Pass | Upload size limit (100 MB), real file-type checks, image-bomb guard (F-088), files stored in private object storage outside the web root, generated names, downloads through authorized routes. |
| **V13 API and web service** | Pass | CORS allow-list; JSON only; public API keys scoped and hashed; inbound webhook signatures verified (F-013); WebSocket authenticated per connection. |
| **V14 Configuration** | Pass | Every setting documented (`docs/CONFIGURATION.md`, 306 settings) and the production template passes them all to the containers (test); API docs closed in production; `Server` header removed; CSP, HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy` on every host, tenant custom domains included. |

**Accepted deviation** (does not block Level 2 in substance): *V14.2.1 (known-vulnerable
components)* — three Python packages with advisories whose vulnerable code path the application
does not reach (§5).

## 5. Twelve-Factor review

| Factor | Status | How |
|---|---|---|
| I. Codebase | Yes | One repository, one deployable application (API, workers, web bundle); historical material in `archive/`. |
| II. Dependencies | Yes | Exact pins for Python and npm; per-image requirement subsets generated from the main file and checked by a test. |
| III. Config | Yes | Everything from environment variables through one settings class; production template and `docs/CONFIGURATION.md`; start-up refuses missing or weak secrets. |
| IV. Backing services | Yes | Postgres, Redis, S3-compatible storage, SMTP and model providers attached by URL and credentials only. |
| V. Build, release, run | Yes | Multi-target Docker build (`web`, `worker`, `enrich`, `ocr`); migrations run by a separate one-shot service; RUNBOOK step 11 pins the deployed image digests for rollback. |
| VI. Processes | Yes | Stateless API processes: sessions in Postgres, limits in Redis, files in object storage. |
| VII. Port binding | Yes | The API serves itself on port 8000; Caddy only routes. |
| VIII. Concurrency | Yes | Web, worker, enrichment and OCR are separate process types, scaled independently. |
| IX. Disposability | Yes | Fast start, graceful shutdown (pending SLO measurements flushed, F-102), at-least-once jobs with idempotency and an outbox. |
| X. Dev/prod parity | Yes, with one note | Same Postgres 16 + pgvector and Redis everywhere; the browser suite runs the production build under the production CSP. Note: this environment has no Docker daemon, so the production compose stack itself is exercised first on the VPS (RUNBOOK). |
| XI. Logs | Yes | One event per line on stdout, context fields included, optional JSON (`LOG_FORMAT=json`); the proxy's access log on stdout with token paths redacted. |
| XII. Admin processes | Yes | Migrations, retention sweeps, backups and drills are scripts in the same codebase, run as one-off containers or from the shipped cron file. |

## 6. Database

- **One head**: `q1a1_retired_groq_models`. Phase 3 adds one data-only migration that moves
  workspaces off Groq models Groq retired (F-196); no schema change.
- **Drift**: 0 new operations; the 283 known lines are undeclared indexes and constraints that exist
  in the database and not in the models (harmless, ratcheted so they can only shrink; F-017).
- Every test database is built by `alembic upgrade head`, so every migration ran thousands of times.

**Accepted dependency advisories** (pip-audit, unchanged): `torch` 2.12.1 (the advisory needs loading
an untrusted model file, which the app never does; 2.13 is planned with the CPU-only image, F-045),
`setuptools` 81 (pinned below 82 by torch), `paramiko` 3.5.1 (no fixed release; the SFTP client only
connects to configured ERP hosts with pinned host keys).

## 7. Open items after launch (none blocks it)

| Item | Why it is not a blocker | Next step |
|---|---|---|
| F-006 production stack never started here | No Docker daemon in this environment | RUNBOOK §9 on the VPS |
| F-125 Stripe test-mode prices and webhook secret; N-027 Postmark | Configuration the owner holds | Before the first paying customer |
| N-032, N-033 plan placement of Batch operations and TruthMesh | Provisional placements are in force and enforced server-side | Owner decision, one line per tier |
| F-045 images carry the CUDA build of torch | Size, not safety | CPU-only torch on a machine that reaches download.pytorch.org |
| Main JavaScript chunk 412 KB gzipped | Loads once, cached; pages already split | Split the largest vendor libraries |
| `idp_session_sync` stored, read by nothing | The console no longer shows it; signed single logout always applies | Drop the column or build the feature |
| F-011, F-017, F-030, F-124, F-126 | Hygiene and known, documented edge cases | Ongoing |

## 8. Reproduce the evidence

```
# Backend (from backend/, with a Postgres 16 + pgvector and Redis running)
pytest -q
alembic heads
python scripts/check_migration_drift.py
python scripts/normalize_encodings.py --check

# Frontend (from frontend/)
npx tsc -b && npm run typecheck
npm run lint && npm run check:self && npm run build && node scripts/check-no-sourcemaps.mjs --dist
npm audit --omit=dev

# Browser suite (from frontend/): fresh database, model stand-in, production CSP enforced
E2E_LLM=1 e2e/scripts/start-stack.sh
E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e
```
