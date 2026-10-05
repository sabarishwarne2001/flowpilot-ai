# Phase 4 — Live engine hardening and core feature verification

_Branch `hardening/phase-4-core-engines`. Written 2026-10-05 at the Phase 4 checkpoint._

## 1. In plain language

Phase 3 clicked through the product like a person. Phase 4 went underneath: every core engine was
driven **for real** — a PDF is uploaded through the API, the real worker processes it (OCR text
layer, extraction, entity resolution, matching, radar, cases …), the results are read back from the
database and through the API, and permissions are tried with each role. Only the AI model itself is
replaced by a "recorded" one that gives the same answer every time, so the tests are repeatable and
cost nothing.

Where an engine was broken, it was fixed on the spot, and every fix is proven the strict way: a
test that failed before, the fix, the same test passing, and the rest of the suite still passing.

**What it found and fixed:** 26 real defects — among them a team-invite crash for every paying
customer (F-051), a rule that could carry on after a security violation (F-084), an avatar upload
that could take 1.8 GB of server memory (F-088), service-level pages that could never record data
(F-079), a broken invoice-correction path that also crashed the monthly billing job (F-081), a
hanging request that locked the organization (F-078), and automatic approval of a 50/50 split
between verification agents (F-069). Three capabilities you asked for were built: global search
across workspaces (F-091), "send test event" for webhooks (F-080), the page image with each
reading highlighted in the review workbench (F-092), plus bank-account-change and round-amount
flags on the radar (F-093).

**What it proved works as designed** (no defect found): three-way matching, ERP posting formats
and exactly-once, the entity graph, scanned-packet splitting, obligations, process intelligence,
the document corroborator, workflows, the review hub, redaction with zero leakage, the platform
security wall, append-only audit log, team management, BYOK routing, API keys (scopes, rate
limits, revocation), webhooks (HMAC signing, retry), SMTP over verified TLS, SCIM last-owner
protection, sign-in lockout and sign-out-everywhere, and **workspace isolation for 23 features**.

**Stale tests:** about 230 backend tests were red on `main` for reasons unrelated to the product
working (they predated plan gates, used a removed column, had a date baked in, or ran against the
development database). They hid real coverage — tenant isolation, billing, R33 automation safety
among others. They were aligned without weakening any assertion; each plan gate they now pass is
covered by an explicit "Free plan is refused" test instead.

## 2. The live engine harness

`backend/tests/engines/` (conftest + 30 test files):

| Piece | What it does |
|---|---|
| `engines` fixture | an Enterprise tenant with every persona (owner, org admin, workspace admin, contributor, viewer, outsider); `plan("business")` etc. switch plans |
| `RecordedLLM` | answers classification, extraction, summaries and verification agents from recordings keyed by a marker in the document text; per-agent answers for verification |
| `make_pdf` / `process` | builds real PDFs (reportlab), uploads them through the API and runs the worker loop (`drain`) exactly like `worker.run_jobs_loop` |
| `isolation.py` | `assert_workspace_isolated()`: opens a second workspace, sweeps every list route for ANY id workspace A owns, and addresses A's objects through B's URLs (GET/POST/PUT/PATCH/DELETE) |
| recorder | with `ENGINE_ROUTE_LOG` set, logs every API call and drained job; `docs/hardening/tools/coverage_from_engines.py` turns that into `COVERAGE.csv` updates |

CI: a new step **"Phase 4 engine proofs"** runs `tests/engines` and every test repaired or added in
this phase on every pull request (own step, like the Phase 2 proofs, because the full suite stops
at its first 5 failures).

## 3. Engine by engine

| Engine | Result | Proof (backend/tests/…) |
|---|---|---|
| Upload → every engine | works | engines/test_pipeline_live.py |
| Document settings | **fixed F-067** | engines/test_document_settings_live.py |
| Multi-agent verification | **fixed F-068, F-069** | engines/test_verification_live.py |
| Extraction memory | **fixed F-070, F-071; built F-072** | engines/test_extraction_memory_*.py |
| Entity graph | works (merge/unmerge exact, role-gated, tenant-isolated) | engines/test_entity_graph_live.py |
| Cases | **fixed F-073** | engines/test_cases_live.py |
| Scanned packets & dicer | works | engines/test_packets_live.py |
| Tables | **fixed F-074** | engines/test_tables_live.py |
| Obligations & calendars | works | engines/test_obligations_live.py |
| Three-way matching | works (tolerance, reasons, short delivery) | engines/test_three_way_matching_live.py |
| ERP posting | **fixed F-065** (Admin/Owner only, also via review hub); formats, exactly-once under a thread race | engines/test_erp_posting_live.py |
| Process intelligence | works | engines/test_process_intel_live.py |
| Forensic audit radar | **fixed F-075; built F-093** (bank account changed, round totals) | engines/test_radar_live.py, test_payment_risk_live.py |
| Corroborator | works | engines/test_corroboration_live.py |
| Workflows (DAG) | **fixed F-076, F-084** | engines/test_automation_live.py, services/test_arch13_gate_13_5_13_6_engine.py |
| Review hub | works; **built F-092** (page evidence); **fixed F-085** | engines/test_review_hub_live.py, test_review_evidence_live.py |
| Clause assertions | **fixed F-077** | engines/test_clause_assertions_live.py |
| Redaction studio | works, zero leakage (text, object tree, both output modes) | engines/test_redaction_live.py |
| PDF rendering everywhere | **fixed F-066** (6 crash sites) | services/test_pdfium_call_sites_thread_safety.py |
| Global search (Ctrl+K) | **built F-091** | engines/test_global_search_live.py |
| Workspace switcher isolation | works for 23 features | engines/isolation.py (+ every engine test) |
| Team & invites | **fixed F-051, F-078** | api/test_invitation_paid_org.py, engines/test_team_and_audit_live.py |
| Audit log | **fixed F-052**; append-only in the database | api/test_audit_export.py, engines/test_team_and_audit_live.py |
| Transactional email | works: STARTTLS verified before the password, test send arrives, private addresses refused | engines/test_email_live.py |
| Service levels (SLO) | **fixed F-079** | services/test_slo_service.py |
| Data governance (erasure, export) | works; **fixed F-086** | services/test_compliance_service.py, api/test_compliance_endpoints.py |
| BYOK & model routing | works (tenant key + routed model reach the provider); **fixed F-090** | engines/test_byok_live.py, api/test_arch23_endpoints.py |
| API keys | works (shown once, scoped, rate-limited, revocable); **fixed F-083** | engines/test_api_keys_live.py |
| Webhooks | works (HMAC-SHA256, retry after backoff); **built F-080** | engines/test_webhooks_live.py |
| Branding & custom domains | works (vanity host gets its tenant's manifest) | api/test_arch25_endpoints.py |
| Analytics egress (warehouse sync) | works | api/test_arch26_endpoints.py |
| Billing & quotas | **fixed F-081, F-082, F-087**; seats, proration, dunning proven | services/test_arch15_*.py, test_billing_account_lock.py, test_embedding_metering.py |
| Enterprise identity (SCIM) | **fixed F-089**; last-owner race closed | api/test_arch16_scim_dunning.py, services/test_deprovision_last_owner_race.py |
| Sign-in | lockout and sign-out-everywhere proven | engines/test_login_lockout_live.py, test_session_revocation_live.py |
| Avatars | **fixed F-088** | api/test_avatar_upload.py |
| Platform hub (COGS, RevOps, Sovereign) | security wall proven over the live route table: tenant owners/admins get 404 | engines/test_platform_wall_live.py |

## 4. Plans and roles

Plan gating was exercised per feature (Free refused with 402, the right paid plan admitted) in the
repaired suites and the engine tests; role gating with every persona (VIEWER read-only, CONTRIBUTOR
works, ADMIN/OWNER-only actions refused below, platform consoles invisible to tenants).

## 5. What is still open

- Owner decisions: **N-019 (viewers part), N-020 items 2/3/5/7, N-021 to N-025** (`NEEDS-OWNER.md`).
- Engineering: F-099 (a few stale invariants, listed there with reasons), F-053 (viewer sidebar),
  F-054 to F-064 from Phase 3 that were not in this brief.
- Not proven here, needing real services: Stripe/Dodo checkout, real LLM providers, DNS for custom
  domains, warehouse destinations (F-063 still applies; the engines use stand-ins for each).

## 6. Full backend suite: main vs this branch

FULL_SUITE_PLACEHOLDER

## 7. How to run

```
cd backend
pytest -q tests/engines                       # the live engine proofs (about 10 minutes)
ENGINE_ROUTE_LOG=/tmp/r.tsv pytest -q tests/engines \
  && python ../docs/hardening/tools/coverage_from_engines.py /tmp/r.tsv   # refresh COVERAGE.csv
```
