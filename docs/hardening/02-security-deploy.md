# Phase 2 — Security and deployment hardening

Branch `hardening/security-deploy`, written 2026-09-30. Read `STATE.md` first if you are
picking the campaign up; this file is the report on what Phase 2 did, what it proved, and what is
still not safe. Every finding has its full evidence in `FINDINGS.md`; every decision that is yours
is in `NEEDS-OWNER.md` (N-011 to N-018 are new).

## 1. The short version

- **Three urgent bugs are fixed:** documents with no bounding box no longer fail on upload (F-020),
  OCR no longer invents text when the engine crashes (F-019), and a rejected API key is no longer
  echoed back in the error (F-021).
- **The app now refuses to start in production with an unsafe configuration** and lists every
  problem at once (F-003). The three public default secrets that were committed to the code are gone.
- **Tenant isolation was proven, not assumed.** Every one of the 423 organization and workspace
  routes was called as another tenant, in both directions: no leak. Two real holes were found and
  closed (soft IDOR on two document routes, F-031; five routes that lacked a plan check, two of which
  a Free plan could actually use, F-004).
- **Uploads:** three real bugs fixed (PDF page-level scripts survived, oversize uploads crashed,
  `%00` in a search box crashed 19 routes). Everything else that was thrown at the upload path held.
- **Deployment blockers, found by reading the production files as an outsider would:** the API
  container could not start because `gunicorn` was in no requirements file (F-040); 23 settings the
  template tells you to fill in never reached the app (F-047); nothing scheduled the backups and
  retention jobs on a Docker Compose server (F-006).
- **New for your VPS:** encrypted, read-back-verified nightly backups with a weekly restore drill, a
  monitor that tells you when either fails or does not run, a container-based job scheduler, and a
  10-step production guide (`docs/RUNBOOK.md` section 9).
- **Dependencies:** 7 npm advisories fixed, 4 Python packages bumped; the FastAPI/Starlette upgrade
  is the biggest thing left (N-014).
- **What I could not do:** start real containers or talk to real Stripe or Dodo. Everything that
  needs them is listed in section 6 as unverified. The first deploy must be watched.
- **I changed no existing test's assertion.** One existing test file got a fixture in its
  arrangement (F-004); nothing was skipped, weakened or deleted.
- **Your decisions are needed on 8 items** (section 7); the first three matter before launch.

## 2. What was fixed

Severity: P1 must fix before a paid launch, P2 before enterprise sales, P3 quality. "Proof" is the
test that failed before the fix and passes now (the Evidence Rule in `CLAUDE.md`).

| ID | Sev | What was wrong, in plain words | Proof |
|---|---|---|---|
| F-020 | P1 | Any chunk without a bounding box was stored as JSON `null` instead of SQL `NULL`; the database refused it and the whole document failed. 35 nullable JSON columns across 25 models had the same trap. | `tests/services/test_chunk_writer_bbox.py`, `tests/models/test_nullable_json_columns.py` |
| F-019 | P1 | On one OCR engine error the code quietly returned invented text ("FLOWPILOT GATE INVOICE...") as if it had read the page. It now fails honestly. | `tests/services/test_paddle_ocr_honest_failure.py` (6) |
| F-021 | P2 | A rejected BYOK key was repeated back in the 422 response, and so was every other submitted field, passwords included. | `tests/security/test_validation_errors_do_not_echo_input.py` (5; 5 failed before) |
| F-003 | P2 | Three secrets had public default values in the code and nothing refused to start without your own. Production with wildcard CORS, debug logging and password `postgres` booted happily; now it is refused with 12 named problems. | `tests/core/test_production_config_guard.py` (72), `tests/infra/test_production_env_template.py` (24) |
| F-004 | P2 | Two routes let a Free tenant use paid features (mint a public-API key, write the enterprise sign-in policy); three more were ungated. | `tests/security/test_plan_gating_server_side.py` (200+ operations, 26 areas) |
| F-031 | P3 | Two document sub-routes answered 200 for another tenant's or a non-existent document. | `tests/security/test_idor_child_entities.py` |
| F-032 | P2 | An unverified account made for someone else's address could list that address's pending invitations. | `tests/security/test_invitations_and_unverified_accounts.py` |
| F-034 | P3 | Posting Stripe events to `/billing/webhooks/STRIPE` (capital letters) crashed. | `tests/security/test_billing_webhooks.py` (21) |
| F-035 | P2 | PDF page-level actions (JavaScript, Launch, SubmitForm) survived the upload scrub into the stored, downloadable file. | `tests/security/test_upload_attacks.py` (49) |
| F-036 | P2 | An upload over the size limit crashed with a 500 instead of a 413. | same file |
| F-037 | P3 | `%00` in a search box crashed 19 routes with a 500 (and filled the error log). | `tests/security/test_nul_byte_guard.py` (11) |
| F-038 | P2 | Production builds shipped source maps, publishing your original TypeScript. | `frontend/scripts/check-no-sourcemaps.mjs`, run in CI |
| F-039 | P2 | 7 npm advisories fixed; `pypdf` (parses untrusted PDFs), `pyasn1`, `anyio`, `aiohttp` bumped. 9 Python packages left, see N-014. | `npm audit` = 0; `pip-audit` job in CI (advisory) |
| F-040 | P1 | The production API container could not start: `gunicorn` was in no requirements file. | `tests/infra/test_production_compose_commands.py` (13) |
| F-041 | P2 | gunicorn's access log wrote capability tokens (public upload and calendar links), query strings and Referer to stdout. | `tests/infra/test_gunicorn_access_log.py` (6) |
| F-042 | P3 | `/health` said "healthy" with Postgres down. Added `/health/ready`; the production healthcheck uses it. | `tests/api/test_health_readiness.py` (5) |
| F-043 | P3 | Floating image tags (`pg16`, `7-alpine`, `python:3.12-slim`). Pinned. | same file as F-040 |
| F-046 | P3 | Swagger UI and the full OpenAPI route map were public in production. | `tests/security/test_api_docs_not_public_in_production.py` (3) |
| F-047 | P1 | 23 settings in `.env.production.template` never reached a container: the LLM keys, `BILLING_GATEWAY` and every Dodo setting, the price ids, token lifetimes, the upload limit. Also the Enterprise price id the plan seeder needs was not in the template. | `tests/infra/test_production_env_template.py`, `tests/services/test_llm_blank_platform_key.py` (6) |
| F-006 | P2 | The nightly retention sweep, reminders, ERP retries and the database backup were started only by a server cron that a Docker Compose setup does not have. Now: container runner for the sweeps, encrypted verified backups, a restore drill, and a monitor ping. | `tests/infra/test_compose_backup_scripts.py` (20, real PostgreSQL) |
| F-024 | P2 | The production migration step fails on a new database (the ARCH-40 flag). Now an explicit, documented one-time step. Production still needs your N-009 answer. | `tests/infra/test_production_compose_commands.py` |
| F-044 | P3 | No Content-Security-Policy. Added in report-only mode; enforcing it is N-015. | not proven in a browser |
| F-007 | P3 | `LICENSE` written (N-004), binaries and key files now git-ignored. Historical scripts kept: they are in use. | n/a |

Not defects, now guarded by tests: F-012 (the live-review socket never reads a token from the URL,
13 tests), F-013 (all three billing webhook routes verify signatures), F-022 (AI tool selectors
carry a tenant scope, 7 tests), F-023 (the client IP is not spoofable through `X-Forwarded-For`,
7 tests).

## 3. What was checked and held (no change needed)

- **Cross-tenant isolation:** 423 org/workspace operations, tenant A against tenant B's real ids,
  every method: only 401/403/404, never 2xx, never 5xx. A mutation control turns the membership
  check off and the sweep then finds crossings, so the pass is not vacuous
  (`test_cross_tenant_sweep.py`).
- **Object-level (IDOR):** 16 object collections and 47 child routes (F-031 was the one gap).
- **Roles:** every route's required role was discovered from its dependencies; every persona below
  it is refused (`test_role_matrix.py`). Platform (super-admin) routes refuse every tenant role
  (39+ operations); partner routes refuse non-members.
- **Tokens:** forged, unsigned (`alg: none`), wrong-secret, tampered, expired, wrong-type and
  malformed tokens all answer the same 401 (`test_auth_token_attacks.py`, 28). Session rotation,
  reuse detection, logout-everywhere and per-device revocation are covered by the existing session and
  auth-endpoint tests, which I re-ran and did not change.
- **Sign-in rate limit, against real Redis:** guessing is cut off, before the password is checked,
  fails closed, cannot be dodged with a forged header (`test_rate_limits.py`, 4). See F-048.
- **Billing webhooks:** real Stripe and Standard-Webhooks (Dodo) signatures: valid event recorded
  once, replay acknowledged without a new row, forged/stale/tampered/oversized refused, no secret
  configured means nothing is trusted.
- **Uploads:** HTML, executables, scripts, SVG, empty, truncated and mislabeled files, encrypted and
  corrupt PDFs, decompression bombs, path-traversal filenames, archive bombs, XML entity attacks,
  15-minute presigned links.
- **Injection:** 300+ SQL-injection probes across every string parameter on tenant routes; 63 SSRF
  destinations in every notation an attacker uses; tenant-typed Azure and SMTP hosts; hostile
  document text in automations.
- **Secrets in history:** all 113 commits scanned, no real credential (FINDINGS, "Secrets scan").
- **Host-header poisoning: not possible** (by reading, no test): every emailed link (password reset,
  email verification, email change, invitations, obligation digests) is built from the configured
  `FRONTEND_URL`, never from the request's `Host` or `X-Forwarded-Host`, and the token rides in the URL
  fragment (`#token=`), so it does not reach server logs or a `Referer` header.

## 4. Numbers

| | Before (Phase 1) | After Phase 2 |
|---|---|---|
| Backend tests passed | 2,285 | **2,769** |
| Backend tests failed / errors | 219 / 33 | **214 / 33** |
| New tests written | | **494** in 35 files (`tests/security`, `tests/infra`, `tests/core` and others), 11 of them from the parallel session |
| Failures that are new since Phase 1 | | **none**: all 247 red test ids were already red in Phase 1; 5 tests that were red now pass |
| Verification gates pass / fail / skip | 39 / 33 / 6 (Phase 1 record) | 38 / 34 / 6 on `main` and 37 / 35 / 6 on the branch, measured here. 77 of 78 gates have identical results; the 78th differs only because of data left by the previous gate run (F-029), so **no gate regressed** |
| Alembic heads | 1 | 1 (no migration was added) |
| `npm audit` advisories | 7 | 0 (tsc, lint, build and the source-map check also pass) |
| Python packages with advisories | 13 | 9 (`pip-audit`; see N-014) |
| Encoding gate | pass | pass (1,971 tracked files) |

The test numbers are one full serial run of the whole suite at commit `a388cd4` (25 minutes, real
PostgreSQL and Redis): 479 of the new tests were in it and all passed. The 4 rate-limit tests and the 11
tests merged from the parallel session were run separately afterwards and pass; the merge, the CI change
and the documentation came after that run and touch no application code beyond comments. CI now runs
the Phase 2 proofs (547 tests, including the older SAML rig) in a step of their own ahead of the full
suite, whose first five failures would otherwise hide them.

The remaining red tests are the Phase 1 baseline (F-016): stale expectations and environment
problems, listed by name in `01-baseline.md`. Phase 2 neither fixed nor hid them.

## 5. What is still not safe, or not proven (residual risks, most important first)

1. **Starlette 0.41.3 / FastAPI 0.115.6 have 13 known advisories**, including the multipart parser
   that reads uploads. Fixing it is a framework upgrade, not a patch (N-014). *Highest remaining risk.*
2. **Nothing was run on a real server.** The compose file was rendered and the app booted from its
   environment, and the backup scripts ran against a real Postgres, but no container was started.
   Expect the first deploy to teach you something; use RUNBOOK 9.2 step 10.
3. **Billing lifecycle is not proven**: failed payments, dunning, cancellation, downgrades, out-of-order
   events and quota enforcement are decided by reconciler jobs that call the gateways, which are
   blocked here. Signature checking and replay protection are proven.
4. **Dodo's "test event reached a live server" check cannot fire** (F-033) until I see a real payload
   (N-013). Until then keep `DODO_LIVEMODE=false` while testing.
5. **Downgrade policy (N-003)**: after a downgrade a customer can still read and delete paid-feature
   data (API keys, webhooks, domains). It looks deliberate; the tests pin exactly that.
6. **Not built:** malware scanning of uploads, two-factor sign-in, a password on Redis, image-digest
   pinning, alerting on errors (N-017).
7. **CSP is report-only** until someone looks at a real browser session (N-015).
8. **Rate limits on registration and forgot-password** are the generic 300 per minute per address;
   the sign-in limit is stricter than intended and may lock out an office (F-048).
9. **The repository is public and its history holds a 36 MB binary** (`stripe.exe`). The history was
   scanned for secrets (none found in the parts I may read), but `backend/evidence/`, `arch07_*`,
   `arch08_*` and the PDFs are off limits to me and need your eyes (N-016).
10. **Object-level isolation is proven for 16 collections**; the rest need hand-built fixtures (Phase 3).
    Template injection in tenant-edited email and branding templates, and prompt injection end to end
    through the assistant, are not covered.
11. **About 220 tests were already red** before this phase (F-016); a red test can hide a real bug.
    Triage is a Phase 3 job.
12. **Backups of uploaded documents are not automated.** They depend on your bucket settings
    (RUNBOOK 9.4); a self-hosted MinIO on the same disk is not a backup.

## 6. Claims that are unverified

- Any command that starts containers or uses `docker compose exec` / `run`: the backup and sweep
  scripts were exercised through their `FLOWPILOT_PG_EXEC` and `FLOWPILOT_RUNNER` prefixes against
  a native Postgres, not through Docker.
- Caddy's behaviour (HTTPS certificate, headers, CSP, the 404 on `/docs`): configuration read, not run.
- The 10-step first deploy, the disaster restore and the cron installation (RUNBOOK section 9).
- `start_dev.ps1` on Windows (Phase 1 carry-over).
- The ARCH-40 migration procedure against real containers.
- That the `pgsty/minio` and other pinned image tags exist: confirmed on Docker Hub earlier, but no
  image was pulled here.

## 6b. Where I did not do exactly what the brief said

- **Plan gating answers 402, not 403.** The product already answers a plan refusal with HTTP 402 and a
  `CAPABILITY_REQUIRED` or `ADDON_REQUIRED` code, which says the true thing ("your plan does not include
  this"), and the web app may key off it. Every test asserts "refused on plan grounds" by that
  convention. If you want 403, say so: it is a contract change for the frontend.
- **Historical `apply_*` / `verify_*` scripts were not archived.** The brief said to archive them only if
  completely unused. They are used: 14 of your PowerShell runners name the apply scripts, and CI's gate
  job runs the verify scripts (F-007, N-016).
- **No verification gate was repaired or retired** (N-010): I compared the gate list before and after so
  Phase 2 added no failing gate, and left the stale ones for your decision.
- **The sign-in limit was measured, not changed** (F-048, N-018).
- **The push went to `hardening/security-deploy`**, the branch you named, not to the session's default
  branch name, and never to `main`. Another Claude session had already pushed its own F-019 and F-020
  fixes to that branch at 08:37 UTC; I merged them (no force-push, `3b0aad1`). The overlap was
  comment and log-text only, and both sessions' tests pass together. If you started two sessions on
  purpose, fine; if not, check that the other one has stopped.

## 7. What I need from you (in order)

1. **N-009:** does a production database with real customer data exist yet? (Decides the migration
   step for production.)
2. **N-011:** confirm the plan table and prices (Free $0; Developer $49; Business $299; Enterprise
   $799 per seat per month, self-serve).
3. **N-016:** open `backend/evidence/`, the `arch07_*`/`arch08_*` files and the PDFs and confirm no real
   customer data or credentials are inside. If unsure, make the repository private now.
4. **N-014:** approve the FastAPI/Starlette upgrade as the first job of Phase 3.
5. **N-012:** is "up to 24 hours of data lost" acceptable for launch?
6. **N-013:** one real Dodo test webhook (body and headers, secret removed).
7. **N-015, N-017, N-018:** CSP enforcement, the extras list, the sign-in limit.
8. **N-002, N-003, N-005:** carried over from earlier phases.

## 8. How to re-run the proofs

```bash
cd backend
pytest tests/security tests/infra tests/core/test_production_config_guard.py -q     # the Phase 2 proofs
pytest -q -rfE --maxfail=1000                                                        # the whole suite (about 25 minutes)
python scripts/run_all_gates.py --static-only                                        # the verification gates
python ../docs/hardening/tools/scan_history_for_secrets.py                           # secrets in git history
( cd ../frontend && npm audit --audit-level=high && npm run build && node scripts/check-no-sourcemaps.mjs --dist )
```

Use the RAM-disk Postgres recipe from `docs/RUNBOOK.md` section 5, and never run two pytest
processes against one Postgres server without giving one a different `TEST_DB_NAME`.
