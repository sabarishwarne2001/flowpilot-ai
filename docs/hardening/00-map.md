# 00 — System map, risk ranking and repo hygiene (Phase 0)

Everything here comes from **reading the code** on commit `1a959a1`. Nothing
was run, so nothing here counts as verified. The ledger
(`COVERAGE.csv`, 1,306 rows) starts with every row `untested`.

## 1. Architecture on one page

```
 Browser ── React 19 SPA (Vite, TanStack Query, Tailwind)
   │          82 routes · 49 nav entries · guards: PrivateRoute, PublicRoute,
   │          TenantGuard, OrganizationGuard, SuperAdminGuard
   ▼
 Caddy (TLS, on-demand certs for custom domains → /internal/tls/authorize)
   ▼
 FastAPI  /api/v1  — 67 router modules, 555 handlers (+ SCIM at /scim/v2)
   │   auth: JWT access token (HS256, 10 min) + refresh (14 days), API keys
   │   (public gateway), SCIM bearer tokens, SAML/OIDC SSO, webhook signatures
   │   tenancy: /workspaces/{id}/… (270 handlers) and /organizations/{id}/… (154)
   │   plan gating: capability_gate / addon_gate called inside each handler
   ├── PostgreSQL 16 + pgvector (151 Alembic revisions, one linear chain)
   ├── Redis 7 (rate limits, locks, replay guards, stream sessions)
   └── MinIO / S3 (documents, logos, exports)

 Workers  python -m app.worker --loop …
   relay (outbox) · delivery (webhooks, notifications) · stripe (inbound events)
   jobs --profile light | ocr (PaddleOCR) | enrich (SentenceTransformers)
   scheduler (21 recurring job types)
 Reranker  app/reranker (internal, port 8081, token-protected)
 Host cron  deploy/cron.d: 17 sweepers + backups + restore drill (NOT in compose)

 External: Stripe, Dodo Payments, Groq (default LLM), Gemini, local LLM,
   BYOK (OpenAI, Anthropic, Groq, Azure OpenAI, Mistral, Gemini), platform and
   tenant SMTP, SAML/OIDC IdPs, DNS, ACME, ERP (QuickBooks Online, Zoho Books,
   Business Central, S/4HANA OData, NetSuite, generic REST/OData; SFTP with
   X12/UBL/Tally/CSV/XLSX), warehouses (BigQuery, Snowflake, Databricks, S3).
```

### Roles defined in code
- Organization: `OWNER`, `ADMIN`, `BILLING`, `MEMBER`.
- Workspace: `ADMIN`, `CONTRIBUTOR`, `VIEWER`.
- Partner: `OWNER`, `ADMIN`, `ANALYST` (partner portal only).
- Platform: `users.is_superuser` (super admin).

### Plans and what they unlock (from `backend/scripts/seed_quota_tiers.py`)

| Plan | Price in seed | Adds |
|------|---------------|------|
| free | $0 | core extraction/OCR, workspace AI chat |
| developer | $49/mo | Developer API keys, outgoing webhooks, custom branding, custom-domain add-on |
| business | $299/mo | + three-way matching, forensic radar, transactional email, extraction memory, entity graph, cases & scanned packets, tables, obligations, ERP posting, warehouse-sync add-on |
| enterprise | $799/mo | + redaction, clause assertions, calibrated autonomy, enterprise identity (SAML/SCIM), priority SLO, document corroborator, collaborative review, process intelligence, egress lockdown |

Sidebar locks, mapped to the lowest plan that opens them:

| Locked nav item | Capability | Opens at |
|---|---|---|
| Extraction memory, Entity graph, Cases, Scanned packets, Tables, Obligations | extraction_memory, entity_graph, case_intelligence (×2), table_intelligence, obligations | Business |
| Three-way matching, ERP posting, Forensic audit radar | reconciliation, erp_posting, anomaly_radar | Business |
| Transactional email | custom_email | Business |
| Process intelligence, Document corroborator, Clause assertions | process_intelligence, universal_corroborator, semantic_assertions | Enterprise |
| Calibrated autonomy, Egress lockdown, Enterprise identity | calibrated_autonomy, egress_lockdown, enterprise_identity | Enterprise |
| Developer platform, API keys, Webhooks, Branding & custom domains | developer_api, outgoing_webhooks, custom_branding | Developer |

Sanity check against the list you saw in the live app: your sidebar showed
Developer platform, API keys, Webhooks and Branding **unlocked** and
Transactional email **locked**. That matches a tenant on the **Developer** plan.
Every item on your list exists in code.

## 2. Navigation cross-check (the flags you asked for)

- **Nav items with no route:** none. Every workspace nav entry is typed to a
  `ROUTE_PATTERNS` key, so a missing route would fail to compile. Every
  organization and platform entry points to a registered route.
- **Routes with no nav item and no deep link:** none found.
  `redactions/:jobId` is opened from `StartRedactionButton`,
  `procurement/policies` is linked from the matching queue and the command
  palette, and `billing/return` is the Stripe return URL. `/admin` has no index
  page (F-009).
- **Items not in your list but in code:** Tolerance policies (command palette),
  Partner portal (partner members only), Create workspace (palette), theme
  toggle, mobile drawer, redaction studio (deep link only).
- **Nav items whose backend capability does not exist:** none. Every
  `capability.*` used in the UI is registered in `app/core/entitlements.py` and
  granted by at least one seeded tier.
- **Pages calling endpoints that do not exist:** none found by a static scan of
  461 API path strings in `frontend/src` (all 9 "unmatched" hits were comments,
  routes or generated types). This is a heuristic. Phase 3's network-error
  fixture is the real proof.
- **Lock or role mismatches:** see F-005 (Analytics not locked in UI but gated on
  the server; four pages with no plan gating at all) and F-015 (ADMIN can mint
  API keys through the API, but the sidebar treats API keys as OWNER-only).

## 3. Backend inventory in numbers

| | Count |
|---|---|
| Handlers (incl. 1 WebSocket; + 3 reranker) | 555 (+3) |
| Mutating (POST/PUT/PATCH/DELETE) | 305 |
| User JWT / super admin / public / SCIM / API key / signature / public token | 469 / 39 / 21 / 14 / 6 / 3 / 3 |
| Workspace-scoped / organization-scoped | 270 / 154 |
| Plan-gated on the server | 216 |
| Handlers in modules with **zero** API tests (F-002) | ≈230 |
| Worker job types / on the built-in scheduler / host-cron entries | 49 / 21 / 19 |
| Alembic revisions / heads | 151 / 1 (`arch40_step3_contract_ai_settings`, linear, no merges) |
| Backend settings / missing from prod template | 302 / 246 |
| Backend test files / test functions / frontend test files | 131 / 2,123 / 0 |

Critical-journey rows in the ledger (must reach `deep`): auth 94, billing 90,
upload & extraction 46, team & members 43, review queue 40, API keys &
webhooks 38, data governance 22, workflows 20, audit log 6, settings 6.

## 4. Top 15 areas most likely to hide serious bugs (risk-ranked)

1. **Plan gating on the server (revenue leak).** Each of the 216 gated handlers
   calls the gate by hand inside the function body. It is not a router-level
   dependency, so a new handler that forgets it is silently free. Known gaps
   to prove: F-004.
2. **Tenant isolation / IDOR in the ARCH-31..50 modules.** About 230 handlers
   (ERP, obligations, cases, tables, corroboration, entities, radar,
   procurement, process intelligence…) resolve child ids (`case_id`,
   `posting_id`, `work_item_id`…) inside a workspace, and none has an API test
   (F-002). A single missing `workspace_id` filter lets tenant A read tenant B's data.
3. **Billing correctness.** Three webhook entry points (F-013), a separate
   inbound worker loop, dunning, seat sync, add-on grace, Dodo proration. None
   of the webhook paths is tested. The risks are money lost or double-charged,
   and plan state that drifts from what the customer paid for.
4. **Secrets and production config.** Default secrets are used silently
   (F-003). 246 settings go unset in production (F-011). If `ENVIRONMENT` is
   forgotten, the app runs in development mode.
5. **Authentication and sessions.** JWT and refresh rotation, revocation,
   step-up re-auth, login back-off, SAML (an XSW rig test exists) and OIDC,
   SCIM tokens, invitation and email-change tokens, and the WebSocket token in
   the URL (F-012).
6. **File upload and parsing.** PDFs through PaddleOCR, archive expansion
   (`batch.expand_archive` is a zip-bomb risk), XML (UBL/X12, where
   `erp/formats/xmlsafe.py` exists), the public `/request/:token` upload with no
   account, and presigned URL lifetimes.
7. **SSRF and egress.** Outgoing webhooks, ERP HTTP/SFTP targets, warehouse
   destinations, BYOK base URLs, tenant SMTP hosts and SSO metadata URLs all
   reach addresses the tenant chooses. An `ssrf_client` and "egress lockdown"
   exist; both need adversarial tests.
8. **Prompt injection into actions.** Document text reaches the LLM, which
   drives the automation engine, the process-intelligence "exception agent"
   (it proposes and can auto-apply resolutions) and assistant tools. A
   malicious PDF could trigger an automation.
9. **Background job reliability.** Retention, erasure, ERP retries and backups
   depend on host cron (F-006). Idempotency of 49 job types, dead-letter
   handling, and the stuck-pipeline sweeper are all untested for ARCH-31+.
10. **Super-admin surfaces.** 39 cross-tenant handlers (unit economics,
    sovereign, RevOps: price books, contracts, promos). Every one depends on
    `require_superadmin`. Any slip exposes every tenant's data.
11. **Partner portal and marketplace.** 27 partner handlers authorize in the
    function body (not by dependency) and read across a "book" of organizations.
    Marketplace installs admit third-party workflow code into a tenant's
    automation engine.
12. **Data governance.** Export, erasure, retention and legal holds are
    irreversible actions. An erasure that misses a table (vectors, S3 objects,
    audit details) is a GDPR problem.
13. **No working CI and thin tests.** CI on `main` has not run a single job
    recently (F-001), and the frontend has no tests. Nothing currently catches a
    regression in the areas above.
14. **Frontend guards and states.** The route-level role guard is unused
    (F-008), `/admin` is empty (F-009), and slug collisions exist (F-014). The
    risk is blank pages or error toasts instead of permission screens. This is
    UX, not a leak.
15. **Migrations.** 151 revisions, plus many `fix_*`, `repair_alembic_dag`,
    `reconstruct_dropped_table` and `restore_clean_*` scripts. That history
    suggests manual database surgery, so model-vs-schema drift is likely.
    Downgrades are untested.

## 5. Repo hygiene

| Problem | Where | Plan |
|---|---|---|
| 38 MB `stripe.exe` in git history | commits `d790047` → deleted in `1884891` | Add ignore rule (Phase 2); history rewrite is N-005 |
| Empty `README.md` and `LICENSE` | repo root | README in Phase 1; license is N-004 |
| `requirements.txt` is UTF-16LE + CRLF, full Windows `pip freeze` | `backend/` | Phase 1: convert to UTF-8 if the install proves it is needed; split prod/dev |
| `requirements-dev.txt` holds only `pytest-env` | `backend/` | Phase 1 |
| 5 `apply_hardening_*.py`, 20 `run_*.ps1`, `reset_dev.ps1`, `start_dev.*` | repo root | Phase 2: archive only what nothing runs |
| 27 `apply_arch*.py` (up to 4 MB), 30 `verify_*.py`, `arch07_*`, `arch08_*`, `evidence/` | `backend/` | Off-limits per CLAUDE.md. Phase 2 checks references (CI runs `scripts/run_all_gates.py`) |
| ~150 historical `verify_*`, `patch_*`, `fix_*`, `mutate_*` scripts | `backend/scripts/` | Same as above |
| 10 `ARCH-*-FINAL-CERTIFICATION.md`, `GA-2-REPORT.md`, `FlowPilot-AI-ARCH-41-to-50.md` | repo root | Off-limits. Their claims are not evidence for this campaign |
| 1.37 MB PNG favicon and logo | `frontend/public/` | P3: compress later |
| `frontend/.env.example` wrong (F-010) | `frontend/` | Phase 2 |

## 6. How the ledger was built (repeatable)

`docs/hardening/tools/regen_ledger.sh` rebuilds `COVERAGE.csv` from the code:
- `extract_endpoints.py` parses every router with Python's `ast`. It resolves
  mount prefixes from `router.py` and `main.py`, and classifies auth, roles,
  tenant scope and plan gating from signatures, module aliases and same-module
  helper gates.
- `config_vars.py` lists every `Settings` field and checks both env templates.
- `build_ledger.py` adds pages, nav items, seed UI actions, jobs, cron
  entries, migrations and integrations.
- `xcheck_frontend_api.py` matches frontend API strings against the endpoint list.

Limits: roles resolved inside function bodies (partner portal, some owner
checks) show as `any authenticated user`, and Phase 2 tests settle them. The
`ui_action` rows are a **seed list** of 55 journey actions. Phase 3 expands
them per page from the real rendered buttons. Rows are marked
`[CRITICAL:<journey>]` in `description` where they belong to a journey that
must reach `deep`. `test_file` entries starting with `existing:` are pytest
files that look relevant but have **not** been run yet.
