# Phase 1 — Boot and honest baseline

_Date: 2026-09-30. Branch `hardening/baseline`, PR #2._

This is what builds, starts and passes **today**, measured by running it, not
by reading code. Anything not run is marked *unverified*.

## 1. One-screen summary

| Area | Result | Evidence |
|---|---|---|
| GitHub CI starts at all | **Fixed.** Before: 97 of 97 runs rejected with zero jobs | run 98 was the first ever to create jobs (F-001) |
| Encodings (44 files) | **Fixed**, 0 findings | `normalize_encodings.py --check` locally and in CI |
| Postgres 16 + pgvector, Redis, MinIO | **Up** (Docker) | all three `healthy`, bucket created |
| Backend install | **Works on Python 3.12**; fails on 3.11 | `scipy==1.18.0` needs 3.12 |
| Alembic: heads | **1 head** (`arch40_step3_contract_ai_settings`) | `alembic heads`, CI |
| Alembic: upgrade on empty DB, downgrade -1, re-upgrade | **Pass** (with `ARCH40_CONTRACT=1`) | local and CI job "Migration head & drift" ✅ |
| Alembic: autogenerate drift | **Fail: 314 operations** (recorded as baseline, not fixed) | F-017 |
| API starts, `/api/v1/health` | **200 healthy** | curl |
| OpenAPI schema | **200**, 441 paths, 550 operations; all 550 match the ledger | §5 |
| Worker starts | **Yes**, sweeps run, 0 errors | worker log |
| Upload → OCR → embed → COMPLETED | **Works with `ML_STUBS=true`**; real models cannot download here | §4 |
| Backend tests (2,546) | **Red:** 2,285 passed, 219 failed, 33 errors, 9 skipped of 2,546 | §6, F-016 |
| Verification gates (78) | **Red:** 39 pass, 33 fail, 6 skip | §7, F-029 |
| Frontend `tsc` / lint / build | **Pass** (locally and in CI) | §8 |
| Browser automation (Playwright) | **Works here:** headless Chromium logs in and renders the dashboard | §9 |

**CI on PR #2 (latest run):** Encoding ✅, Frontend ✅, Migration head & drift ✅,
Verification gates ❌ (33 real gate failures), pytest ❌ (real test failures;
CI stops at the first 5), CI aggregate ❌. The two red jobs are red because of
the product and test state described below, not because of CI setup. Fixing
them is Phase 3/4 work.

## 2. Environment used

- Cloud container: Linux, 4 CPUs, Docker 29.3.1 (daemon started by hand), Python
  3.12.3 (3.11 is the system default and cannot install the pins), Node 22.22.2.
- Services from `backend/docker-compose.yml`: PostgreSQL 16.15 with pgvector
  0.8.6, Redis 7.4.11, MinIO `pgsty/minio:RELEASE.2026-08-04T00-00-00Z`.
- Backend packages: `requirements.txt` + `requirements-dev.txt` installed into a
  venv, 6.5 GB (torch 2.12.1 with CUDA 13 libraries alone is about 3.9 GB).
- Network: `pypi.org`, `registry.npmjs.org` and Docker Hub are reachable.
  **Blocked:** `huggingface.co`, `aistudio.baidu.com`, `modelscope.cn`,
  `bcebos.com` (model weights), `api.stripe.com`, `api.groq.com`,
  `test.dodopayments.com`, `quay.io`, `dl.min.io`.

## 3. What was fixed in Phase 1 (startup, build, CI, environment only)

One logical fix per commit, each proven failing before and passing after:

| Commit | Fix | Finding |
|---|---|---|
| `fix(ci): use a single-quoted string…` | GitHub rejected `ci.yml` (`","` inside `${{ }}`) | F-001 |
| `fix(encoding): normalise the 44 files…` | UTF-16 `requirements.txt` + 43 BOMs | F-001 |
| `fix(compose): replace the removed minio/minio…` | pinned `pgsty/minio`, `pgsty/mc` | F-018 #7, N-008 |
| `fix(gates): give the two unphased verification gates…` | gate runner exited 2 before running any gate | F-018 #8 |
| `fix(ci): run on Python 3.12…` | 3.12, `POSTGRES_*` env, `ARCH40_CONTRACT=1` in CI | F-018 #3–5 |
| `fix(ci): make the migration drift gate able to fail…` | drift gate grepped a file that never existed | F-017 |
| `fix(dev): let the local launchers migrate…` | `start_dev.sh`, dev compose, `start_dev.ps1` (was 12 migrations short) | F-018 #5–6 |
| `feat(dev): labelled ML test stubs…` + `fix(dev): make the OCR stub…` | `ML_STUBS=true` | §4 |
| `fix(tests): import decrypt_password…` | a collection error stopped the whole pytest run | F-018 #9 |
| `fix(tests): let a plain pytest migrate…` | `ARCH40_CONTRACT=1` in `pytest.ini` | F-018 #5 |
| `fix(dev): install backend packages, set the reranker token, seed tiers…` | launchers never ran `pip install`, compose needs `RERANKER_INTERNAL_TOKEN`, tiers were never seeded | F-018 #10 |

No product feature code was changed except the two `ML_STUBS` hook points,
which are off by default and refused in production. No test expectation was
changed.

## 4. Model weights and the ML stubs

PaddleOCR and SentenceTransformers (`all-MiniLM-L6-v2`) **cannot download their
weights in this environment** (403 from the network policy for every model
host). The real models were therefore **not exercised** in Phase 1.

`ML_STUBS=true` (off by default; the app refuses to start with it in
production) swaps in labelled stubs (`backend/app/services/ml_stubs.py`):

- embeddings: deterministic 384-dimensional hashed bag-of-words vectors, stored
  with `embedding_model = 'flowpilot-test-stub-embedding'`;
- OCR: the real Paddle provider with only the model call replaced. Digital PDFs
  keep their real text layer; image and scanned pages become
  `[FLOWPILOT TEST STUB OCR - NOT REAL TEXT] sha256=…`.

**Proven end to end** (dev stack, API + worker, `ML_STUBS=true`): a PNG, a
scanned PDF and two digital PDFs from `backend/evaluation/corpus/` were uploaded
through `POST /workspaces/{id}/work-items` (201) and reached `COMPLETED`, with
chunks, 384-d embeddings and bounding boxes stored. With the first version of
the stub (boxes missing), every upload failed. That exposed real bug F-020.

**What the stubs cannot tell you:** OCR accuracy, extraction quality, retrieval
relevance and AI-assistant answer quality. Those need the real models, on a
machine that can download them (your PC or GitHub runners).

## 5. API and OpenAPI

- `GET /api/v1/health` → 200 `{"status":"healthy",…}`.
- `GET /api/v1/openapi.json` → 200, OpenAPI 3.1.0, 441 paths, 550 operations.
- All 550 operations match an endpoint row in `COVERAGE.csv`. The ledger has 8
  rows that OpenAPI does not list, and each was checked:
  - 3 belong to the separate reranker service (`reranker:8081`);
  - 1 is the review-collaboration WebSocket (WebSockets are not in OpenAPI);
  - 4 are hidden from the schema on purpose and are mounted:
    `POST /billing/stripe/webhook` and `POST /billing/webhooks/stripe` → 400
    "Missing Stripe-Signature header"; `POST /billing/webhooks/dodo` → 401;
    `GET /internal/tls/authorize?domain=example.com` → 404 `NOT_VERIFIED`.
    (Manual probes only; the ledger rows stay `untested`.)

## 6. Backend tests

**Result (full serial run, same settings as CI but without `--maxfail`):**

| Passed | Failed | Errors | Skipped | Total | Time |
|---|---|---|---|---|---|
| 2,285 | 219 | 33 | 9 | 2,546 | 18 min 52 s |

(2,546 = the 2,538 tests that existed before Phase 1, plus the 8 new ML-stub
tests, which all pass. Run on the RAM-disk Postgres from RUNBOOK §5 with
`ML_STUBS=false`; on a normal disk the same run takes about 2 hours.)

Grouped by the first error line (details and severities in FINDINGS F-016):

| Count | Cause | Main files |
|---|---|---|
| 47 | No published quota tier / price book "in force" in the test DB (seed data wiped per test) | `test_arch15_gate_15_*` |
| 38 | `ADDON_REQUIRED` / `CAPABILITY_REQUIRED`: plan gating newer than the tests' tenant plan | `test_arch25_endpoints.py`, `test_arch26_endpoints.py` |
| 25 | `relation "users" does not exist`: tests on a separately named database that nothing migrated | `test_arch0g_endpoints.py`, `test_usage_api.py` |
| 18 | Constructor signature drift (`AISettings`, `LLMReservation`, `FactSet`) | BYOK / LLM / assertion tests |
| 7 | `slo_measurements.observed_value` written as NULL | `test_slo_service.py` |
| 4 | Network blocked in the sandbox (model download) | would pass where Hugging Face is reachable |
| ≈110 | Individual assertion failures (public API, isolation coverage, automation timeouts, BYOK, e-mail settings, …) | see `pytest -rfE` output |

Notable single failures: `test_a_rejected_key_is_not_echoed` (secret echoed,
F-021); `test_data_isolation.py` reports workspace-scoped collections with no
isolation test (`verifications`, `usage`, `extraction-memory`, `erp`,
`entities`, and more); SCIM writes the client address `"testclient"` into an
`inet` column.

**How the number was reached.** A first run split into 3 parallel shards gave
337 failures + errors. 85 of those passed when re-run alone: some tests share
a fixed database name, so parallel runs collided (F-030). The single serial
run above is the baseline to compare against.

## 7. Verification gates

`python scripts/run_all_gates.py --static-only` (after the ordering fix): **39
pass, 33 fail, 6 skip** of 78. The same in CI. Failing gates, with the first
failure line the runner printed (the gate files themselves were not opened,
per CLAUDE.md):

```
verify_migration_history.py: <unknown>:1: SyntaxWarning: invalid escape sequence '\d'
verify_models_step2.py: [FAIL] users unexpected columns: ['avatar_file_id', 'display_name', 'locale', 'timezone', 'timezone_source']
verify_step4.py: verify_step4.py: error: the following arguments are required: --phase
verify_arch06_step0.py: sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "company_logo_url" does not exist
verify_platform_email.py: ImportError: cannot import name 'PlatformEmailNotConfigured' from partially initialized module 'app.core.platform_email' (mo
verify_arch07_step0.py: sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "company_logo_url" does not exist
verify_arch07_step3.py: [FAIL] Unexplained AUDIT site app/api/v1/work_items.py:482 event=KNOWLEDGE_REINDEX_REQUESTED.
verify_arch07_step6_preflight.py: [FAIL] Step 6 pre-flight failed: (psycopg2.errors.UndefinedColumn) column w.company_logo_url does not exist
verify_arch08_step1.py: [FAIL] S1.1: company_logo_url still present in app/ outside schemas/workspace.py
verify_arch08_step1_preflight.py: sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "company_logo_url" does not exist
verify_arch08_step6.py: [FAIL] More than 1 file reads x-forwarded-for: ['core/client_ip.py', 'services/identity/session_policy_service.py', 'api/v1/sa
verify_scope_vocabulary.py: [FAIL] S.3   EVERY ApiKeyScope value is actually grantable to a real row  -- these scopes exist in ApiKeyScope but CANNOT 
verify_arch09_step2.py: [SKIP] No organization exists.
verify_arch09_step4_5.py: [SKIP] No organization exists.
verify_arch09_step6.py: [SKIP] no organization exists
verify_arch09_step7.py: [SKIP] no organization exists
verify_arch09_step8.py: [FAIL] A.5    POST /endpoints refuses a forbidden-namespace event type  -- AttributeError: module 'app.api.deps' has no attrib
verify_arch09_step10.py: [PASS] C.7    a claimed job's lease expires and is reaped back to FAILED
verify_arch10_step0.py: InternalError: (psycopg2.errors.InFailedSqlTransaction) current transaction is aborted, commands ignored until end of transact
verify_arch10_step9.py: [FAIL] ARCH-10 is not closed.
verify_arch11_step0.py: httpx.ProxyError: 403 Forbidden
verify_arch13.py: - only in python:    ['billing.seat_added', 'billing.seat_removed', 'billing.seat_sync_needed', 'identity.domain_lapsed', 'identity.
verify_arch14.py: FAIL     db_every_row_priced                 Rows written since 14.1 carry no price.
verify_arch19.py: 22 passed, 1 failed, 0 skipped
verify_arch20.py: [ FAIL ] G10 every model module with a table is imported by app/models/__init__ — unregistered: ['organization_addon']
verify_arch21.py: FAILED: 1 of 22 checks failed.
verify_arch23.py: ============================================================================
verify_arch28.py: [SKIP] G2 refusal outcomes stay inside ck_sso_assertion_outcome                  --static-only
verify_arch29_slice4.py: [FAIL] G11 no un-themeable colours in components        hardcoded colours found in 3 files
verify_arch29_tranche3.py: [FAIL] 29T3-G2 conversation menu escapes overflow       Assistant.tsx does not use PortalMenu
verify_arch30_tranche1.py: [FAIL] 30T1-G4 entitlement readers and labels use registered keys      capability.anomaly_radar has no KNOWN_METERS entry; 
verify_arch30_tranche2.py: [FAIL] G15  with BILLING_GATEWAY=DODO, checkout never touches Stripe and carries tenant metadata — CheckoutGatewayUnavailab
verify_arch0v.py: [FAIL] 0V-G13 Every registered tool selector carries a TenantScope
```

Gates that point at real problems are logged as F-022, F-023 and F-025.
Several others check a schema that later migrations removed
(`company_logo_url`), need an argument, or need database rows even under
`--static-only`. Deciding which gates still count needs the owner → N-010.

## 8. Frontend

| Check | Local | CI |
|---|---|---|
| `npm ci` | ✅ | ✅ |
| `npx tsc --noEmit` | ✅ 0 errors | ✅ |
| `npm run lint` (`--max-warnings=0`) | ✅ | ✅ |
| `npm run build` | ✅ built in 17 s | ✅ |

Notes for later phases (not blockers):
- Main bundle 1.23 MB minified (359 kB gzip); Vite warns about chunks over 1 MB.
- The production build emits source maps (`index-*.js.map` is 5.4 MB). If they
  are deployed, the full frontend source is public. Phase 2 decides.
- `npm audit`: 7 vulnerabilities (1 moderate, 6 high). Direct dependencies
  involved: `postcss`, `react-router-dom`. Phase 2 (dependency audit).

## 9. Can Phase 3 (Playwright browser tests) run here?

**Yes, in this cloud environment**, with two limits.

Evidence: Chromium 1194 is preinstalled at `/opt/pw-browsers`. Playwright
1.56.1 (installed in a scratch folder, not the repo) launched it headless,
opened `http://localhost:5173/login`, completed the two-step login as
`admin@flowpilot.ai`, and landed on `/flowpilot-dev/default` with the sidebar,
the upload area, document counts and recent activity rendered. The only
console errors were four 404s for the user avatar (F-028), which the Phase 3
fixture would flag.

Limits here, and what to do about them:
1. **Real model output** (OCR accuracy, RAG answers): blocked hosts. Phase 3 runs
   with `ML_STUBS=true` and tests the product's behaviour, not model quality.
   Model quality checks need your PC (or GitHub runners, which can reach
   Hugging Face).
2. **Live third-party calls** (Stripe test-mode checkout pages, Groq LLM
   answers, Dodo): blocked. Billing state changes can still be tested fully by
   sending locally signed test webhooks to the API, which is also more
   deterministic. The AI assistant needs an LLM stub for end-to-end runs here.
   An actual Stripe test-mode checkout in a browser must run on your PC.

Also on your Windows PC: `start_dev.ps1` has never been run by the campaign
(there is no Windows here). The Phase 1 changes to it are small and mirror the
tested `start_dev.sh`, but they are **unverified**.

## 10. Not verified in Phase 1

- Real PaddleOCR / SentenceTransformers output (weights blocked).
- `start_dev.ps1` on Windows.
- `docker-compose.prod.yml` and the production images (Phase 2).
- The reranker service (`app/reranker`), not started.
- Any ledger row other than the 151 migrations and 2 new config rows. Nothing
  else was upgraded, because no test mapping exists yet (Phase 3).
