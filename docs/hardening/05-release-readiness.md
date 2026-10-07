# FlowPilot AI — Release-readiness certification (final release)

_Date: 2026-10-06 · Branch: `hardening/final-commercial-release` · Head migration: `p6a3_user_mfa_factors`_

## Verdict

**GO for production deployment.**

The application is release-ready: every finding that can be fixed in code is fixed and proven by a
test, every open owner decision has been taken (NEEDS-OWNER.md, "Final release"), both full test
suites pass with no failures, the database has exactly one migration head and no unexpected drift,
the frontend builds, lints and type-checks clean, and the whole product runs end to end in a real
browser under the production security headers with a model attached.

What this verdict does **not** cover, because it cannot be done from this environment, is the
deployment itself: building the four images and starting the stack on the VPS (no Docker daemon
here, F-006). That is the first-deploy procedure in `docs/RUNBOOK.md` §9, and its smoke checks are
part of deploying, not a condition on the code. Run them on the day.

In plain words: the code is ready to sell. Deploy it by following the runbook step by step, watch
the checks it lists, and you are live.

---

## 1. What this release changed

**Built (owner decisions N-002 to N-020, taken under founder authority)**
- Two-factor sign-in with an authenticator app and recovery codes (N-017).
- Notification category filters, mark as unread, delete (N-020 item 2).
- Promo code at checkout, priced per plan (item 3).
- Invite people from Organization → Members with roles and workspace access (item 5).
- Document viewer: the page image beside the extracted fields, each field correctable in place,
  corrections teaching extraction memory and written to the audit log (items 6, 7).
- Plan gating and role visibility as decided: Service levels targets are Enterprise (N-002);
  Workflows, Run history and Review queue hidden from viewers (N-019); Webhooks, Audit log and
  Enterprise identity shown to admins (N-006).
- Production hardening: Content-Security-Policy enforced (N-015), Permissions-Policy, Redis
  password, sign-in allowance 20 per address per 5 minutes (N-018), uptime heartbeat, image
  digest pinning in the runbook, historical scripts archived (N-016).

**Fixed (FINDINGS.md F-108 to F-122)** — two P1s: a legal hold did not stop three ways of
destroying a document (F-108), and a new workspace's first document skipped its AI steps (F-113).
Nine P2s and four P3s, among them the nightly backup verify deleting good backups (F-109), the
review hub calling every held item a disagreement (F-111), the assistant posting into the wrong
conversation (F-112), the radar flagging every goods receipt (F-115), and four authentication
gaps found by the ASVS audit below (F-117 to F-120), pages rebuilt (and typing lost) when the
profile arrived (F-121), and API responses without `Cache-Control` (F-122).

**Repository** — 286 historical files moved to `archive/` unchanged (N-016); the root holds only
the application, its tests, deployment files and documentation.

## 2. Evidence

| Check | Start of this release | Final | Command |
|---|---|---|---|
| Backend test suite | 3,223 passed, 0 failed, 9 skipped | **3,298 passed, 0 failed, 9 skipped** | `pytest -q` (backend) |
| Browser suite (Playwright, Chromium) | 272 passed, 7 failed, 2 skipped (no model, CSP off) | **290 passed, 0 failed, 1 skipped** (by design) | `E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e` |
| Production CSP enforced in the browser suite | not run | **0 violations** (the strict fixture fails a test on any console error) | `E2E_CSP=1` |
| Alembic heads | 1 (`p5a1`) | **1** (`p6a3_user_mfa_factors`) | `alembic heads` |
| Model/migration drift | 283 known, 0 new | **283 known, 0 new** | `scripts/check_migration_drift.py` |
| Frontend type-check | clean | **clean** (app and e2e) | `npx tsc --noEmit -p .` / `-p e2e` |
| Frontend lint | clean | **0 errors, 0 warnings** | `npm run lint` |
| Frontend production build | builds | **builds; no source maps** | `npm run build`, `check-no-sourcemaps.mjs --dist` |
| npm advisories (production deps) | 0 | **0** | `npm audit --omit=dev` |
| Python advisories | 5 in 3 packages | **5 in 3 packages, accepted** (§5) | `pip-audit -r requirements.txt` |
| Text encoding gate | clean | **clean** | `scripts/normalize_encodings.py --check` |

The browser suite's one skip is by design: "the AI provider is down" runs only when no model is
attached. Model-dependent flows (classification, extraction, tables, entities, three-way match,
radar, corroboration, assistant answers) run against `frontend/e2e/support/llm-mock.mjs`, a
deterministic OpenAI-compatible stand-in, in every run and in CI (F-063).

**Every module tree was exercised live** in the browser suite: work items and the document viewer,
extraction memory, entity graph, cases, packets, tables, obligations, procurement three-way match,
ERP posting, process intelligence, radar, corroborator, automation, review hub, clause assertions,
redaction, assistant, dashboard and notifications, settings (legal holds included), tenant switcher,
public portals, marketplace and partner, calibrated autonomy, organization management, platform
admin, authentication, and the plan (Free, Developer, Business, Enterprise) and role (Owner, Admin,
Member, Viewer) matrices with their 402 paywalls and "Access restricted" screens.

## 3. OWASP ASVS 4.0.3 — Level 2

Status per chapter. "Pass" means the controls the chapter asks for at Level 2 are in place and
tested; deviations are listed with the reason they are accepted.

| Chapter | Status | Evidence |
|---|---|---|
| **V1 Architecture** | Pass | Tenancy model, trust boundaries and data flows in `00-map.md`, `02-security-deploy.md`; one public-route registry (`app/core/public_route_registry.py`) that every unauthenticated route must be listed in, enforced at start-up. |
| **V2 Authentication** | Pass | Passwords: 12+ characters, checked against breached and common passwords with zxcvbn, no composition rules, strength meter, show-password (F-119, V2.1.1–2.1.12). Argon2id at the OWASP floor (19 MiB, t=2). Generic sign-in errors; per-address limit, per-(address, account) refusal ladder and per-account slow-down (V2.2.1). Two-factor sign-in, TOTP RFC 6238 with replay protection, recovery codes hashed, wrong codes capped (V2.8). Reset tokens single use, short-lived, never in a query string (V2.5). |
| **V3 Session management** | Pass | Opaque refresh tokens (hashed at rest) rotated on every use with reuse detection and family revocation; 10-minute access tokens with a `type` claim; HttpOnly, Secure, SameSite=Lax cookie scoped to the auth path (V3.4). Sign-out, sign-out everywhere, device list with revoke; password change revokes every other session (V3.3.1, V3.3.3). Re-authentication 12 hours after sign-in or after 30 idle minutes (F-120, V3.3.2). Step-up re-authentication for the billing portal. |
| **V4 Access control** | Pass | Every route authorizes on the server: role matrix, cross-tenant, IDOR-on-child-entity and super-admin-only sweeps generated from the OpenAPI schema, so a new route is tested the day it is added (`tests/security/`). Plan gating server-side (402 `CAPABILITY_REQUIRED`). Tenant isolation suite. |
| **V5 Validation, sanitization, encoding** | Pass | Pydantic schemas on every body; injection sweep and NUL-byte guard over every route; validation errors never echo input; SQL only through the ORM or bound parameters; outbound requests through an SSRF-checked client (`test_ssrf_client.py`); React escaping plus DOMPurify for the one rendered-Markdown view; CSP enforced as the second line (V5.3). |
| **V6 Stored cryptography** | Pass | Secrets at rest (SMTP passwords, BYOK keys, TOTP secrets) encrypted with MultiFernet and rotatable keys; API keys and recovery codes stored as HMAC-SHA256 with a pepper; tokens from `secrets`; the JWT secret is required and checked against known-leaked values; production refuses to start with weak or default secrets (`production_guard.py`). |
| **V7 Errors and logging** | Pass | One error envelope `{code, message}` with no stack traces; every sign-in, refusal and second-factor challenge logged with the client address and, once known, the account id (never the password, code or token); an audit log per organization of security-relevant changes with actor, outcome and IP (two-factor on/off and recovery-code use, roles, plans, keys, deletes, field corrections); log lines carry context with secret-looking keys masked and token paths redacted in the access log (F-114). |
| **V8 Data protection** | Pass | Legal holds stop every destroying path (F-108); retention and GDPR erasure with an audit trail; API responses `Cache-Control: no-store` unless a handler asks for caching (F-122, V8.2.1); no source maps shipped; `index.html` not cached. |
| **V9 Communications** | Pass | TLS everywhere through Caddy (automatic certificates, HSTS with preload, HTTP redirected); internal traffic stays on the private Docker network. |
| **V10 Malicious code** | Pass | Locked dependency versions (`requirements.txt`, `package-lock.json`); no third-party script or CDN in the page (CSP `script-src 'self'`); `npm audit` clean; pip-audit in CI; secret scanning of the history (`scan_history_for_secrets.py`). |
| **V11 Business logic** | Pass | Rate limits per route family with standard headers; idempotency keys on uploads and bulk actions; spend limits on AI usage; invitation resend cooldown; calibrated autonomy holds risky automation for review. |
| **V12 Files and resources** | Pass | Upload size limit (100 MB), real file-type checks, image-bomb guard (F-088), files stored in private object storage outside the web root, generated names, downloads through authorized routes. |
| **V13 API and web service** | Pass | CORS allow-list; JSON only; public API keys scoped and hashed; inbound webhook signatures verified (F-013); WebSocket authenticated per connection. |
| **V14 Configuration** | Pass | Every setting documented (`docs/CONFIGURATION.md`, 306 settings) and the production template passes them all to the containers (test); API docs closed in production; `Server` header removed; CSP, HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy` on every host, tenant custom domains included. |

**Accepted deviation** (does not block Level 2 in substance): *V14.2.1 (known-vulnerable
components)* — three Python packages with advisories whose vulnerable code path the application
does not reach (§5).

## 4. Twelve-Factor review

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

## 5. Database

- **One head**: `p6a3_user_mfa_factors`. This release adds three additive migrations:
  `p6a1_work_item_field_corrections` (field corrections, audit resource type `WORK_ITEM`),
  `p6a2_review_extraction_reasons` (review-queue view v9, F-111), `p6a3_user_mfa_factors`.
- **Drift**: 0 new operations; the 283 known lines are undeclared indexes and constraints that exist
  in the database and not in the models (harmless, ratcheted so they can only shrink; F-017).
- Every test database is built by `alembic upgrade head`, so all migrations ran thousands of times.

**Accepted dependency advisories** (pip-audit): `torch` 2.12.1 (the advisory needs loading an
untrusted model file, which the app never does; 2.13 brings a new CUDA stack and is planned with
the CPU-only image, F-045), `setuptools` 81 (pinned below 82 by torch), `paramiko` 3.5.1 (no fixed
release; the SFTP client only connects to configured ERP hosts with pinned host keys).

## 6. Open items after launch (none blocks it)

| Item | Why it is not a blocker | Next step |
|---|---|---|
| F-006 production stack never started here | No Docker daemon in this environment | RUNBOOK §9 on the VPS |
| F-045 images carry the CUDA build of torch | Size, not safety | Build with the CPU-only torch on a machine that reaches download.pytorch.org |
| Organization-wide "require two-factor" switch | Two-factor is available to every user today | Next feature after launch |
| Main JavaScript bundle 382 KB gzipped | Loads once, cached; pages already split | Split the largest vendor libraries |
| F-011, F-017, F-030 | Hygiene: template defaults, ratcheted drift, a 30-minute test suite | Ongoing |

## 7. Reproduce the evidence

```
# Backend (from backend/, with a Postgres 16 + pgvector and Redis running)
pytest -q
alembic heads
python scripts/check_migration_drift.py
python scripts/normalize_encodings.py --check

# Frontend (from frontend/)
npx tsc --noEmit -p . && npx tsc --noEmit -p e2e
npm run lint && npm run build && node scripts/check-no-sourcemaps.mjs --dist
npm audit --omit=dev

# Browser suite (from frontend/): fresh stack with the model stand-in, production CSP enforced
E2E_LLM=1 e2e/scripts/start-stack.sh
E2E_LLM=1 E2E_CSP=1 npx playwright test -c e2e
```
