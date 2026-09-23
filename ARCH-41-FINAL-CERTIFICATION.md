<!-- ARCH41-S3:certification -->
# ARCH-41 — Final Certification

**Milestone:** Operational Seams, Automation Conformance & Extraction Memory (Tranches 1–3)
**Accepted starting points:** `2f81cdf` (Production Hardening) or `6f7dc4c` (ARCH 41 Tranche 1)
**Database:** `hm1_tier_price_per_key → arch41_step1_extraction_memory → arch40_step3_contract_ai_settings` (held). One file head. Release head: `arch41_step1_extraction_memory`.
**Capability:** `capability.extraction_memory` — Business and Enterprise.

## Status

Certified in the build sandbox (Ubuntu, PostgreSQL 16.13, pgvector 0.8.1, the full migration chain replayed on an empty database). **Final sign-off requires one passing `.\run_arch41.ps1 -AllowUnpriced` on the Windows 11 development machine**; append its evidence (`backend\evidence\arch41\verify_arch41.json`, `automation_conformance_live.json`) to this file.

## Results

| Suite | Result |
|---|---|
| `verify_arch41.py --db --mutate --build` | **75 / 75** — 20 offline, 13 database, 40 mutation, 2 build |
| Live automation conformance matrix | **ALL CONFORMANT** — 14 triggers and 7 actions authored over HTTP, emitted through the real outbox, run by the real job handler, read back from the timeline routes; every node COMPLETED; 8 capability-gated triggers/actions refused (422) |
| `verify_arch40.py --db --regression` (chains 38 → 37 → 39 → 36 → 35 → 34 → 33 → 32 → 31 → 31_step0) | **46 / 46** |
| `verify_hardening_final.py` | **3 / 3** |
| `apply_arch41.py` on clean `2f81cdf` (LF), `6f7dc4c` (LF), `6f7dc4c` (CRLF) | 66 operations (30 whole files, 36 anchored edits); second run writes nothing; rollback leaves no residue; result byte-identical to the verified tree; CRLF and BOMs preserved |
| Tier seeding | Price book v1 → pre-ARCH-41 tiers v1 → ARCH-41 seed with `--carry-forward`: Business v2 and Enterprise v2 carry the capability, Free and Developer unchanged, carry-forward ran (no subscriptions existed in the sandbox) |
| Frontend | `tsc -b` 0 errors, `vite build` clean, `eslint --max-warnings=0` on all 14 ARCH-41 console files |

## What the gates prove

**Redaction Studio.** Previews load through the API client with the session, abort superseded renders, revoke every object URL, never cache the unredacted page; burned previews change URL when enabled regions change; a back link exists in the header, lock and error states; downloads mint fresh presigned URLs. Drawing: pointer capture on the surface, regions inert while drawing, 8-pixel minimum, Escape cancels, touch scrolling suppressed. The preview DPI floor (75) is separate from the burned-output floor (150, the `redaction_jobs` CHECK).

**Automation.** Static: 14 triggers, 15 events, 7 commercial actions, capability keys and excluded actions mutually consistent; every event has a production emitter (twins resolved through vocabulary constants); the timeline knows every execution status and polls every 2 s while anything is in flight. Live: the conformance matrix above.

**Backup floor.** AES-256-GCM chunked encryption refuses bit flips, truncation, extension, wrong key, a dropped final chunk, reordering and header edits; retention keeps exactly 7 daily + 4 weekly; dump and row counts share one snapshot; no credential reaches argv; the drill restores into a scratch database, matches every table's count, detects a mismatch and refuses to drop the source.

**Extraction Memory (live, one rolled-back transaction).**
- M1 SHADOW default changes no extraction; six invoices share one layout, a bill of lading gets its own.
- M2 review harvest writes encrypted exemplars, CORRECTED vs CONFIRMED, sensitive values as shape only, and the document's correction outcome.
- M3 the AUTO sweep learns rules in SHADOW with their replay record and starts a trial at five reviewed documents.
- M4 memory reaches the ON arm (and its verification agents), never the OFF arm.
- M5 both trial arms are held from auto-approval; triage sends an agreed document to full review.
- M6 the trial promotes on proven improvement; the ACTIVE layout applies memory; conformal tenants are held until recalibration.
- M7 the schema refuses an injected control arm, an unproven ACTIVE rule, two running trials and a cross-workspace member.
- M8 an ACTIVE rule right 10 times in 25 is retired for drift.
- M9 HTTP: 402 without the plan, the `/potential` teaser, summary counts, mode write, trial sentence, provenance chip.
- M10 deleting a document deletes its memory.

Statistics: one-sided Mann–Whitney U equal to SciPy to 1e-9 including ties; one-sided 95% Wilson lower bound (52/52 promotes, 51/51 does not); per-field non-inferiority. Mutations prove each of these fails when broken, including four on the live path (harvest labelling, the API gate, the autonomy hold, the control arm).

## Defects found and fixed during certification

1. **Wrong baseline head.** The database head after hardening is `hm1_tier_price_per_key`, not `arch40_step2a_review_view_paths`. ARCH-41 was placed after hm1 and before the contract step, re-parenting it; `run_arch41.ps1` handles a database whose contract already ran.
2. **Render DPI.** Lowering `MIN_RENDER_DPI` to 75 fixed previews but let the API accept job resolutions the database refuses (HTTP 500) and failed an ARCH-32 gate. Split into `MIN_PREVIEW_DPI = 75` and `MIN_RENDER_DPI = 150`.
3. **Memory never ran at extraction time.** The engine read `work_item.organization_id`, which does not exist; the fault barrier swallowed the error. Resolved through the workspace.
4. `AuditOutcome.SUCCESS` → `ALLOWED`; `CalibrationModel` → `CalibrationModelVersion`; the new page registered in `verify_arch36.GATED_PAGES`; head pins widened in nine earlier verifiers.

## Not verified in the sandbox

- The Windows 11 run (PowerShell runner, `pg_dump` from `PG_BIN`, `npm.cmd`).
- Browser acceptance of the drawing canvas and the Extraction Memory page.
- Carry-forward moving a real live subscription (the logic is the hardening phase's and is gated there; no subscription existed here).
- Seeding with real gateway price ids (`-AllowUnpriced` was used).

## Run and roll back

```
.\run_arch41.ps1 -CheckOnly
.\run_arch41.ps1 -AllowUnpriced
.\run_arch41.ps1 -Rollback                        # code
python -m alembic downgrade hm1_tier_price_per_key  # database (drops the seven tables)
```
