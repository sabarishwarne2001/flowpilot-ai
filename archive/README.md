# Archive: historical development records

Nothing in this folder runs the product, deploys it or tests it. It is kept for reference: the
record of how FlowPilot AI was built phase by phase (ARCH-01 to ARCH-50 and the hardening tiers)
before the hardening campaign in `docs/hardening/`.

Moved here in the final-release hardening (Stage 3), unchanged: every file keeps its original
path under `archive/`, so `archive/backend/scripts/verify_arch31.py` was `backend/scripts/verify_arch31.py`.
`git log --follow` shows each file's history across the move.

| What | Was | Why it is no longer active |
|---|---|---|
| `apply_*.py`, `backend/scripts/apply_*`, `patch_*`, `fix_*`, `mutate_*`, `restore_clean*`, `restore_pristine*`, `force_fix*`, `repair_alembic_dag`, `reconstruct_dropped_table`, `reset_outbox_migration` | one-off scripts that wrote each phase's code and migrations, or repaired them once | their effect is in the code and the migration chain; running one again would rewrite current code |
| `run_arch*.ps1`, `run_hardening_*.ps1` | PowerShell runners that called the `apply_*` and `verify_*` scripts of one phase | same as above |
| `verify_*.py`, `backend/scripts/run_all_gates.py` | static "gates" that checked one phase's invariants by reading source | superseded by the pytest suites (security, isolation, engines, infra: over 3,200 tests run by CI); 34 of 78 had gone stale (FINDINGS F-029). Retired from CI by owner decision N-016. Two gate modules stay in `backend/scripts/` because active tests import them: `verify_arch14.py`, `verify_arch15.py` |
| `debug_*`, `diagnose_*`, `find_migration_sql_error`, `inspect_arch16_integration`, `audit_arch01_09`, `audit_for_claude`, `ga2` | investigation tools of a past incident or report | the incidents are closed |
| `*-FINAL-CERTIFICATION.md`, `FlowPilot-AI-ARCH-41-to-50.md`, `GA-2-REPORT.md` | phase reports | the current state is `docs/hardening/` (`05-release-readiness.md`) |
| `backend/evidence/`, `backend/arch07_*`, `backend/arch08_*`, `backend/arch40_db_probe.py` | evidence dumps and probes of ARCH-07, ARCH-08 and ARCH-40 | one-off records; the owner confirmed they hold no customer data or credentials (N-016) |

The archive is outside `backend/`, so it is not in the container images' build context.
