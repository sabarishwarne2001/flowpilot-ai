# Hardening campaign — STATE

_Last updated: 2026-09-30 (end of Phase 1)_

## Current phase
**Phase 1 — Boot and honest baseline: COMPLETE. Stopped at the Phase 1
checkpoint.** The next session starts Phase 2. Do not redo Phase 0 or 1.

## What is done
- Phase 0: `00-map.md`, `COVERAGE.csv` (now 1,308 rows), `FINDINGS.md`,
  `NEEDS-OWNER.md`. PR #1 (merged).
- Phase 1: PR #2 from `hardening/baseline`
  (https://github.com/sabarishwarne2001/flowpilot-ai/pull/2).
  - CI runs for the first time in the repository's history. Encoding,
    Frontend and Migration head & drift are green on GitHub; Verification gates
    and pytest are red because of real gate and test failures (F-016, F-029).
  - Fresh-clone blockers fixed (F-001, F-018): workflow syntax, 44 encodings,
    Python 3.12, `POSTGRES_*` in CI, `ARCH40_CONTRACT` for throwaway/dev
    databases, removed MinIO images, gate runner ordering, a stale test import,
    and launchers that never installed packages, generated the reranker token
    or seeded plan tiers.
  - `ML_STUBS=true` stubs for OCR and embeddings (model hosts are blocked
    here), proven end to end with 4 uploads reaching COMPLETED.
  - Migrations: 1 head; upgrade → downgrade -1 → upgrade pass locally and in
    CI; drift is 314 operations, recorded in `backend/alembic/drift_baseline.txt`
    and enforced as a ratchet (F-017).
  - Tests: full serial run in progress (numbers pending) (F-016).
  - Gates: 39 pass / 33 fail / 6 skip (F-029).
  - Frontend: tsc, lint, build pass (locally and in CI).
  - Playwright + headless Chromium work here (login and dashboard proven).
  - Docs: `01-baseline.md`, `docs/RUNBOOK.md`, `README.md`.
  - Ledger: 151 migration rows → `smoke`; 2 config rows added (`ML_STUBS`,
    `ARCH40_CONTRACT`). Nothing else upgraded.
  - FINDINGS: F-016..F-030 added. NEEDS-OWNER: N-001 resolved; N-004, N-006
    and N-007 decided by the owner; N-008..N-010 added.

## Next action (exact)
Phase 2 on branch `hardening/security-deploy`, per THE_MASTER_PROMPT.
Start with the confirmed or high-impact items that Phase 1 surfaced:
1. F-021 (BYOK key echoed in 422). A failing test already exists
   (`tests/api/test_byok_endpoints.py::TestCredentialConfidentiality::test_a_rejected_key_is_not_echoed`).
   Fix the validation error handler so it never echoes `SecretStr` inputs, then
   sweep other secret fields.
2. F-019 (OCR fabricates text on the oneDNN/PIR error): write a failing test,
   remove the shim, make the job fail.
3. F-022 (agent tool selector without tenant scope) and F-023
   (`X-Forwarded-For` read in 5 places): prove or disprove each with a
   cross-tenant / forged-header test.
4. Then the Phase 2 list in THE_MASTER_PROMPT (secrets incl. F-003,
   production config, auth, tenancy, plan gating incl. F-004, uploads, billing
   webhooks, dependency audit incl. 7 npm vulns, containers incl. source maps
   and the CUDA torch in the `web` image, repo hygiene incl. LICENSE (N-004)).
5. Production scheduling for the host-cron jobs (F-006) on Docker Compose/VPS
   (N-007), and `ARCH40_CONTRACT` in production once N-009 is answered.

Before starting: read `docs/RUNBOOK.md` §3 and §5. Use the RAM-disk Postgres
recipe for test runs, and never run two pytest processes against one Postgres.

## Blockers
None for Phase 2. Open owner decisions: N-002, N-003, N-005, N-008, N-009,
N-010, and the copyright-holder name in N-004. None blocks Phase 2 from
starting. N-009 blocks only the production-migration fix, and N-010 blocks
only the gate triage.

## Budget notes
Phase 1 budget was about $15. Phase 1 installed the full stack, ran all 2,538
tests several times (sharded, rerun and a final serial run), ran all 78 gates,
and drove the app through the API and a browser. The exact spend is not
visible from inside the session; check your usage page. If Phase 1 went over,
consider trimming Phase 2 to the P1/P2 items above.

## Environment notes (for the next session)
- Branch pushes to `hardening/*` work from the cloud session.
- Docker is installed but its daemon is not running at start: run
  `nohup dockerd >/tmp/dockerd.log 2>&1 &`.
- The sandbox exports `AWS_ACCESS_KEY_ID=proxy-injected`. Before
  `docker compose up`, export `AWS_ACCESS_KEY_ID=minioadmin
  AWS_SECRET_ACCESS_KEY=minioadmin` (F-027), plus `JWT_SECRET_KEY` and
  `RERANKER_INTERNAL_TOKEN`.
- Use Python 3.12 (`uv venv -p /usr/bin/python3.12`; use
  `UV_HTTP_TIMEOUT=600` for the torch download). System python3 is 3.11.
- Model hosts, Stripe, Groq and Dodo are blocked; set `ML_STUBS=true`.
- `pkill -f <pattern>` can kill the calling shell when the pattern appears in
  the command line itself; use anchored `pgrep -f '^…'` and kill by PID.
