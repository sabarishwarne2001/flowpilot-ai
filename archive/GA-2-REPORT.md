<!-- ARCH50-S1:ga2-report -->
# GA-2 — the whole verification chain on a clean database

**Verdict: GA-2 PASSES — 36 suites, 29 PASS, 7 KNOWN, 0 FAIL** on a database created from nothing (`flowpilot_ga2`), in 9 min 54 s, one suite after another — run through the delivered `run_arch50.ps1 -GA2` itself (PowerShell 7.4.6). Every KNOWN result failed only the gates its reason names, and every one of them reproduces identically on the ARCH-49 baseline (b76347a, run the same way on its own clean database). GA-2 also found **five defects** — three earlier verifiers that ARCH-50 itself had broken, and two product defects that predate ARCH-50 — and **four stale gates**; all nine are fixed (§3). Evidence: `backend/evidence/ga2/` (`summary.txt`, `ga2.json`, `run_arch50_GA2.log`, one log per suite, `setup.json`, the automation conformance report, a copy of every evidence file a suite rewrote) and `backend/evidence/ga2/baseline/` (the same run on b76347a).

## 1. What was run, and how

`python scripts/ga2.py --database flowpilot_ga2 --create` (on Windows: `.\run_arch50.ps1 -GA2`):

1. **A clean database.** `DROP DATABASE … WITH (FORCE)`, `CREATE DATABASE`, then `alembic upgrade arch50_step1_sovereign_revops` from an EMPTY schema through every revision (never `head`: the lossy contract step `arch40_step3_contract_ai_settings` stays held) — 208 tables in about 5 s; `scripts/seed_price_book.py`; `scripts/seed_quota_tiers.py --allow-unpriced` (a clean database has no gateway price ids). Nothing else: 0 organizations.
2. **Every suite, one after another** (never two database suites at once), with `POSTGRES_DB` and `DATABASE_URL` pointed at that database: `verify_arch41` … `verify_arch50 --db`; the five hardening harnesses `--db` (master, final, tier1, tier2, tier3); `verify_arch40` … `verify_arch34`, `verify_arch31`, `verify_arch31_step0 --db` (each chains its own regressions); the script verifiers `verify_arch16`, `verify_arch30_tranche1`, `verify_arch30_tranche3`, `verify_arch12`, `verify_arch25`, `verify_arch21`, `verify_arch09_step4_5`; `pytest tests/test_ssrf_client.py` and `tests/services/test_arch12_budget_and_isolation.py`; the live automation conformance run; and the two ARCH-10 verifiers that must never touch a database you back up (`verify_arch10_step3`, `verify_arch10_step9`) — `ga2.py` runs those only against a database whose name contains `ga2` or `scratch`.
3. **Triage is mechanical.** PASS is exit 0. KNOWN means the suite failed and EVERY failed gate is one its entry in `ga2.KNOWN` names — a known-failing suite that fails one more gate is `FAIL (beyond the known failure)`. Anything else is FAIL, and the run exits 1.

The baseline was reproduced the same way: a worktree of b76347a with the same `ga2.py` pointed at `arch49_step1_process_intelligence` and its own clean database (`flowpilot_ga2_base`), minus `verify_arch50`.

## 2. Result

| Suite | ARCH-50 (final) | b76347a |
|---|---|---|
| `verify_arch41` … `verify_arch48 --db` | PASS (33, 24, 27, 28, 29, 31, 37, 24) | PASS (same counts) |
| `verify_arch49 --db` | PASS 30/30 | **FAIL 29/30** — A1: `sweep_process.py` committed without its executable bit (§3.7) |
| `verify_arch50 --db` | PASS 33/33 | — |
| `verify_hardening_master --db` | KNOWN (D1, D7) | KNOWN (D1, D7) |
| `verify_hardening_final --db` | KNOWN (Payment loop (Stripe)) | KNOWN (same) |
| `verify_hardening_tier1`, `tier2 --db` | PASS | PASS |
| `verify_hardening_tier3 --db` | KNOWN (ARCH-38 idempotency) | KNOWN (same) |
| `verify_arch40`, `39`, `37`, `36`, `35`, `34`, `31`, `31_step0 --db` | PASS (35 and 34: 110/110) | PASS |
| `verify_arch38 --db` | KNOWN 38/39 (A1) | KNOWN 38/39 (A1) |
| `scripts/verify_arch16`, `30_tranche3` (8/8), `12`, `25` (21/21), `09_step4_5` | PASS | PASS |
| `scripts/verify_arch30_tranche1` | KNOWN 10/11 (30T1-G4) | KNOWN 10/11 |
| `scripts/verify_arch21` | KNOWN 21/22 (G8) | KNOWN 20/22 (G8, **G16** — fixed, §3.6) |
| `tests/test_ssrf_client.py`, `test_arch12_budget_and_isolation.py` | PASS 18/18, 24/24 | PASS |
| automation conformance (live) | PASS — ALL CONFORMANT | PASS |
| `scripts/verify_arch10_step3` | PASS 19/19 | **FAIL 17/19** — S1.6, S2.3 (stale gates, §3.8) |
| `scripts/verify_arch10_step9` | KNOWN 20/21 (G5.3) | **FAIL 19/21** — G2.2 (a product defect, §3.5), G5.3 |
| **Totals** | **29 PASS · 7 KNOWN · 0 FAIL** | 26 PASS · 6 KNOWN · 3 FAIL |

## 3. What GA-2 found, and what was done

### ARCH-50 had broken three earlier verifiers — fixed (each only visible when the whole chain runs)

1. **`verify_arch47` N1 / MS24** pinned the ERP HTTP client's constructor text (`SSRFSafeHTTPClient(connect_timeout=…)`). ARCH-50 builds that client through the egress gate (`egress.http_client(egress.ERP_HTTP, …)`, whose `GuardedHTTPClient` subclasses `SSRFSafeHTTPClient`). Widened (`ARCH50-S1:n1-widened-47`, `ms24-widened-47`): either form is accepted, the gate's class must be an `SSRFSafeHTTPClient` and the factory must return one; MS24's anchor follows the form in the file, so the mutation still bites (`verify_arch47 --mutate`: 72/72).
2. **`verify_arch39` B1** replaces `LLMService._execute_query` with a test double whose signature stopped at `byok_client`; ARCH-50 passes the calling `organization_id` (so its egress lockdown governs the provider hosts). The double now follows the signature (`ARCH50-S1:fake-widened-39`).
3. **`verify_arch36` A2** — the handoff said to register every capability-locked page in `verify_arch36.GATED_PAGES`, and ARCH-50 registered `organizationEgress`. But that registry is for WORKSPACE navigation entries (its parser reads `{ id, route, capability }` items); the organization menu's entries are pushed without an id, so the registration made A2 fail ("gated page whose entry shows no lock") — and with it the regression chains of `verify_arch37` and `verify_arch38`. The registration is removed; `verify_arch50` T1 checks the organization entry instead (it advertises `CAPABILITY.egressLockdown`, its page enforces the same key, and it stays out of GATED_PAGES). No organization entry (custom email, developer API, branding, autonomy, identity) was ever registered there.
4. **(Found by the first GA-2 attempt, earlier in the build.)** The cron-run sweeps `scripts/sweep_process.py` (ARCH-49) and `scripts/sweep_erp_postings.py` (ARCH-47) could not enqueue anything: run from cron they are not the worker, and `job_service.enqueue()` refuses every job type without the handler registry (`UnknownJobTypeError`) — their own gates had called `register_all()` themselves, so they never saw it. Both (and the new `sweep_revops.py`) now register the handlers first (`ARCH50-S1:sweep-registers-handlers`); `verify_arch50` MS31 and X2 run the REAL cron entry point.

### Product defects older than ARCH-50 — fixed

5. **The database refused seven pipeline transitions the application declares legal** (`verify_arch10_step9` G2.2, failing since 2026-09-06). `app/services/pipeline_state.STAGE_TRANSITIONS` gained re-queueing from EXTRACTING / EXTRACTED / ENRICHING and retrying from FAILED or QUOTA_BLOCKED straight into EXTRACTING / ENRICHING, but the trigger function `work_items_stage_transition_guard()` kept ARCH-10's fourteen pairs, so PostgreSQL raised 23514: re-running a quota-blocked document's extraction after its limit was raised, or retrying a failed document's enrichment, failed in the database instead of proceeding. `arch50_step1_sovereign_revops` now installs the guard with exactly the application's 21 pairs (and its downgrade restores ARCH-10's body byte for byte). Gated three ways: T2 (parity with `pipeline_state`, the downgrade restores `arch10_step7`'s function), D3 (the REAL function on a scratch table: all 21 legal moves pass, the other 9 are refused) and MS43 / MS44 / MD19.
6. **A rate-limited API call got a 429 without rate limit headers** (`verify_arch21` G16). The per-key 429 is raised in `require_api_key`, which short-circuits the middleware that adds `X-RateLimit-*` / `RateLimit-*`; it now carries them itself (limit, remaining 0, reset, tier — `ARCH50-S1:rate-limit-429-headers`).
7. **A clean checkout of b76347a fails `verify_arch49` A1**: `backend/scripts/sweep_process.py` was committed without its executable bit (cron runs it), so `apply_arch49.py --check` wants to "restore" it. ARCH-50's apply sets the bit. **On Windows, git records it only if told:** after applying, `git add --chmod=+x backend/scripts/sweep_process.py backend/scripts/sweep_revops.py backend/scripts/dr_pitr.py backend/scripts/licence_tool.py backend/scripts/release_manifest.py backend/scripts/ga2.py` before committing.

### Stale gates — the gate, not the product, was wrong; widened to the invariant

8. **`verify_arch10_step3` S1.6** required exactly ARCH-10's three queue specs; ARCH-13 split the outbox into a public and an internal queue and ARCH-15 added the Stripe inbound queue. The invariant is "one claim implementation": every claimer and reaper that exists still delegates to `claim_eligible_rows` / `release_expired_leases` (`ARCH50-S1:s16-widened`). **S2.3** expected an IntegrityError on a second usage write with the same idempotency key; SEAM-I-1 made `record_usage` idempotent (it returns the first event). The invariant — exactly ONE row, no double-bill — is what it now checks (`ARCH50-S1:s23-widened`).

### Known — recorded with reasons (each reproduced identically on b76347a)

| Suite | Gates | Why it is not a defect of this release |
|---|---|---|
| `verify_hardening_master --db` | D1, D7 (D5, D6 passed here) | Live payment-gateway gates. A clean database is seeded `--allow-unpriced`: D1 finds Enterprise without a gateway price id, D7 cannot sync seats to a price that does not exist. They pass with `GATEWAY_PRICE_ID_*` and gateway secrets set. |
| `verify_hardening_final --db` | Payment loop (Stripe) | Needs a real Stripe account and webhook secret. |
| `verify_hardening_tier3 --db` | ARCH-38 idempotency | The same `apply_arch38.py` refusal as below. |
| `verify_arch38 --db` | A1 | `apply_arch38.py` refuses `app/services/ingestion/preset_service.py`, which ARCH-42 edited after ARCH-38 shipped. A historical apply script, not the product. |
| `scripts/verify_arch30_tranche1.py` | 30T1-G4 | The gate wants a `KNOWN_METERS` entry for every entitlement key; a bundled capability is sold inside a plan and is not a meter. The gate predates bundled capabilities. |
| `scripts/verify_arch21.py` | G8 | ARCH-21 reserved `/api/v1/public` for the API-key gateway; ARCH-43's recipient upload link and ARCH-46's calendar feed are token routes under that prefix by design (their own token checks and `POLICY_PUBLIC_READ` — there is no API key to tier). Moving them would break every link already issued. |
| `scripts/verify_arch10_step9.py` | G5.3 | Alembic autogenerate demands a database AT the script head, and the head is the held contract step, so a GA-2 database stops at the release head by design. Column drift is gated per milestone instead (`verify_arch50` D2 and its predecessors). G6.1 / G6.2 need `--with-ocr` (the PaddleOCR models) and were not run. |

## 4. Reproducing it (Windows)

From the repository root, after `.\run_arch50.ps1` (or on its own): `.\run_arch50.ps1 -GA2` (or `-GA2 -GA2Database <name>`). It needs the PostgreSQL server `backend\.env` points at, a role allowed to `CREATE DATABASE`, the `vector` extension available on that server, Redis (several suites use it), and `frontend\node_modules` (`verify_arch37` FE7 executes the flow builder's model with Node). The database it names is dropped and re-created: never point it at one you keep. The summary lands in `backend\evidence\ga2\summary.txt`; afterwards `git checkout -- backend/evidence` restores the per-milestone evidence files the suites rewrote. The run this report describes was made through `run_arch50.ps1 -GA2` under PowerShell 7.4.6 on Linux; Windows PowerShell 5.1 and a Windows filesystem were not available in the sandbox.
