<!-- ARCH43-S1:docs -->
# ARCH-43 — Universal Packet Dicer & Case Intelligence: final certification

**Capability:** `capability.case_intelligence` (Business and Enterprise). **Baseline:** 96f4dd0 ("ARCH 42 DONE").
**Release head:** `arch43_step1_case_intelligence`. Chain: `arch41_step1_extraction_memory → arch42_step1_entity_graph → arch43_step1_case_intelligence → arch40_step3_contract_ai_settings` (the held contract step, still the only file head, never run unasked).

Everything below was run in a Linux sandbox (Python 3.12, PostgreSQL 16.15, pgvector 0.8.1, Node 20). Only results that actually ran are reported. What could not run there is listed at the end.

## 1. What shipped

| Area | Delivered |
|---|---|
| Fair claiming (roadmap §2.2) | `claim_jobs(fair=True)` by default for every worker loop: virtual time = (org's in-flight jobs in this pool + job's rank within its org) / weight; `tenant_queue_weights` (weight, optional `max_inflight`); operator CLI `scripts/queue_weights.py` (list / set / clear / backlog). No tenant API, deliberately. |
| Packet dicer | 12 per-page features (ARCH-41 header-skeleton fingerprints, page-number resets, blank/separator pages, page-classifier changes, …) scored by a logistic model fitted with scikit-learn (`scripts/fit_packet_boundaries.py --write`), coefficients embedded (version pb-1) so scoring runs on the LIGHT profile. Blank and separator pages never start a document. Reviewer-correctable split plans; approval cuts children with pikepdf on the OCR profile. |
| Lineage (roadmap §2.4) | `work_items.parent_work_item_id / parent_page_start / parent_page_end` (composite FK, CHECKs). Children reuse the parent's per-page OCR (no second OCR, no second bill) and go straight to enrichment. The parent's entity mentions are **superseded, never deleted**; a split parent is refused on re-resolution; Entity 360 counts live mentions only. |
| Cases | Templates (required document types + rules) immutable once published (database trigger); versioning (publishing v2 retires v1). DSL: `EQUAL`, `FUZZY_EQUAL` (Jaro-Winkler, default 0.92), `DATE_ORDER`, `WITHIN_DAYS`, `SUM_EQUALS` (sums a field over every document of a type). Assembly by ARCH-42 entity root (kind + optional role) or ARCH-38 batch, or by hand. Status INCONSISTENT > COMPLETE > INCOMPLETE; transitions emit `case.completed` / `case.inconsistent` exactly once per transition. |
| Missing-document requests | 32-byte token shown once; only its SHA-256 stored (CHECK-enforced); consumed by one conditional UPDATE; public upload `GET/POST /api/v1/public/document-requests/{token}` registered in `PUBLIC_ROUTES` with the public rate-limit policy; 410 once used, expired or withdrawn. |
| Triggers | `packet.split`, `case.completed`, `case.inconsistent` (catalog now 17 triggers over 18 events), gated on the capability, in the live conformance matrix. |
| Review hub | Fifth kind SPLIT (reason PACKET_SPLIT); `review_queue_view_v4()` = ARCH-42's v3 + a SPLIT arm; visible only with the capability. |
| Console | Split review (thumbnails through `useAuthorizedBlobUrl`, click or keyboard to toggle a boundary, drag markers between gaps), scanned-packets list, case board, case detail (checklist with one-time upload link, consistency matrix, documents, requests), public upload page `/request/:token`. |
| Operations | Nightly `scripts/sweep_cases.py` (expire requests, re-evaluate open cases) dispatched as `flowpilot-sweep cases` and scheduled at 05:17 (G14). |

**Data (one migration, expand-only):** `tenant_queue_weights`, `packet_splits` (one live plan per document), `packet_split_segments` (generated `int4range`, `EXCLUDE USING gist` — no overlaps — and a trigger keeping segments inside the packet), `case_templates`, `cases` (one live case per template + anchor), `case_documents`, `case_rule_results`, `document_requests`; outbox vocabulary CHECK rebuilt for the three events. Up / down / up proven on an empty database.

## 2. Certification run (final code)

`python verify_arch43.py --db --mutate --build` → **59 passed, 0 failed** (12 offline, 15 live database, 30 mutations, 2 build), 102 s. `python verify_arch43.py --regression` (`verify_arch42.py --db`) → **1 passed**. Evidence: `backend/evidence/arch43/verify_arch43.json`.

The live layer runs in one rolled-back transaction, through HTTP where it matters (402 without the plan, 200/201/409/410/422 with it):

- **F1 fairness under a 10,000-job flood.** Plain FIFO gives all 20 slots of a claim batch to the flooding tenant (the two light tenants would wait ~500 rounds). Fair claiming, with one light tenant at weight 2: every batch split **10 / 5 / 5**; the weight-2 tenant finished in **6 rounds**, the weight-1 tenant in **9**; the flooder was never starved. A claim with ~9,000 jobs pending took 11.8–13.8 ms.
- **F2** a tenant with 20 jobs running yields to an idle one; `max_inflight = 3` caps a tenant outright.
- **P1–P7** schema refusals (overlap, inverted range, out-of-packet, cross-workspace, self-parent, two live plans); detection from stored OCR into the hub; hub approval with corrected boundaries → children with exact page ranges, correct page counts, copied OCR, zero `document.extract` jobs, one `document.enrich` each, original retained, `packet.split` emitted, re-apply idempotent; lineage re-resolution; HTTP incl. a real PNG thumbnail and Entity 360 listing the child, not the packet; dispatch conditions; hub visibility.
- **C1–C5** template validation and database-enforced immutability; entity assembly PO + invoice + GRN → INCOMPLETE → COMPLETE (`case.completed` once, not re-fired on re-evaluation) → INCONSISTENT after a second invoice breaks the PO sum (`case.inconsistent`); batch assembly; tokens (hash only, works once then 410, expired and withdrawn refused, unknown 404); board, matrix, 409 on editing a published template, manual case, close.

**Boundary model, held-out synthetic packets (150 packets, 1,149 boundaries):** precision **0.975**, recall **0.898**, F1 **0.935**; the page-number-only heuristic scores F1 0.714. Gate floors: P ≥ 0.95, R ≥ 0.88, F1 ≥ heuristic + 0.15. Mutation M7 (ignore page-number signals) drives recall below the floor and is caught.

**Mutations (30, all caught):** 11 live (FIFO instead of fair, weights ignored, in-flight ignored, `max_inflight` ignored, hub cannot resolve SPLIT, children re-OCR'd, parent mentions not superseded, lineage guard removed, API gate removed, token not conditionally consumed, cases never complete) and 19 static (capability packaging, missing Entitlement, exclusion constraint removed, two heads, immutability trigger dropped, fair off by default, over-claiming reintroduced, model signals, blank page starts a document, DSL tolerance/threshold, hub leak, pikepdf on LIGHT, double OCR, sweep unscheduled, route ungated, conformance count, thumbnails without the session hook, console type drift). Every mutation's anchor is built before the refusal check, so a drifted anchor fails loudly instead of counting as caught.

**Build:** `tsc -b` + `vite build` clean; `eslint --max-warnings=0` clean on all 17 ARCH-43 console files.

## 3. Packaging proof

`backend/apply_arch43.py`: **89 operations** (42 new files, 47 edits through 104 anchors), generated from `git diff 96f4dd0`; the generator refuses any file without an `ARCH43-S` sentinel and replays every edit against the baseline text to the final sha256.

| Clean clone of 96f4dd0 | `--check` | apply | tree = build | 2nd apply | `--rollback` |
|---|---|---|---|---|---|
| LF | writes nothing | 89 written | all 89 identical | 0 writes, 89 "already applied" | byte-identical over 1,522 files, `git status` clean, backup removed |
| CRLF (every source/text file rewritten CRLF) | writes nothing | 89 written | all 89 identical | 0 writes | byte-identical over 1,522 files; 47/47 edited files kept CRLF |

(`.gitattributes` pins `eol=lf`, so git never checks out CRLF; the CRLF case proven is the one that happens on Windows — files rewritten by an editor or copy.) An applied clone passes `verify_arch43.py` offline.

## 4. Regression (final code, clean database)

`verify_arch42 --db` ✔ · `verify_arch41 --db` 33/33 · `verify_arch40 --db` 45/45 · live automation conformance **ALL CONFORMANT** (17 triggers + 7 actions; the three new triggers COMPLETED and refused with 422 without the plan) · `verify_arch39` ✔ · `verify_arch37` ✔ · `verify_arch36` ✔ · `verify_arch35` ✔ · `verify_arch34` 101/101 · `verify_arch31` ✔ · `verify_arch31_step0` ✔ · `verify_hardening_master` 9/9 · `verify_hardening_final` 3/3 · `scripts/verify_runtime_hardening.py` 14/14 · `verify_arch38` all gates except A1 (below).

## 5. Found and fixed during the build

- **Pre-existing over-claim in `claim_eligible_rows`.** `UPDATE … WHERE id IN (SELECT … LIMIT n FOR UPDATE SKIP LOCKED)` can rescan the limited subquery; the old FIFO path (the production loop before ARCH-43) was seen claiming **32 jobs for `batch_size=20`**. The candidate ids are now locked first and exactly those updated (same locks, same semantics). Guarded by F1 and mutation M19.
- **Model/schema drift in ARCH-43's own models**, found by an autogenerate comparison: `document_requests.token_hash` (`char(64)`) and the generated `packet_split_segments.pages` column. Both fixed; ARCH-43's tables now have zero column-level drift.
- **Two weaknesses in this milestone's own harness**, exposed by its mutations: mutated modules could not load (so three live mutations never ran), and T2 checked only the immutability trigger's name. Both fixed before the run of record.
- **Earlier verifiers pinned to ARCH-42's shape**, widened with `ARCH43-S1:` sentinels, never replaced: head pins (31, 31_step0, 34–42, hardening master), `verify_arch37` emitters/counts, `verify_arch38` B2, `verify_arch40` A6/A8/H1/T1, `verify_arch41` check_catalog, `verify_arch42` E2/D1/E8, `automation_conformance` (17 triggers).

## 6. Pre-existing failures (not ARCH-43; each reproduced on clean 96f4dd0 with its own database)

- `verify_arch38` **A1** — `apply_arch38.py` refuses `preset_service.py`, which ARCH-42 edited after ARCH-38 created it.
- `scripts/verify_arch10_step3.py` (S1.6, S2.3) and `scripts/verify_arch10_step9.py` (G2.2, and G5.3, which requires the database at the file head — the held contract step is never run by design) — historical step scripts the codebase has outgrown.
- **Hazard:** `verify_arch10_step3.py` deletes its test organization under `session_replication_role = replica`, which disables FK cascades and leaves orphaned `audit_logs` rows (80 in the dev database here). Those rows then make **ARCH-41's backup/restore drill (D1) fail** on `pg_restore`. Do not run the ARCH-10 step scripts against a database you back up; to clean one: `DELETE FROM audit_logs a WHERE organization_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = a.organization_id);` (check the count first).

## 7. Not run in the sandbox

- `run_arch43.ps1` on Windows (generated from `run_arch42.ps1`; the Linux equivalents of its check, apply, migrate, seed, build and verify steps ran; its `git update-index --chmod=+x` step did not).
- Clicking through the console in a browser: the pages are type-checked, built and linted, and their API contracts exercised over HTTP, but no UI interaction was driven.
- Real OCR and LLM: packets are synthetic OCR text; the model has not been measured on real scanned 100+ page bundles, so real-world boundary precision/recall is **unmeasured** — reviewer correction exists for exactly this, and `fit_packet_boundaries.py` can refit on corrected plans.
- A live multi-process worker fleet: fairness is proven by consecutive claim rounds in one transaction, not under concurrent workers.
- Production scale beyond the 10,000-job flood.

## 8. Deploying

From the repository root on the server or workstation: `.\run_arch43.ps1` (add `-AllowUnpriced` on a development database). It checks, applies, migrates to `arch43_step1_case_intelligence` (stamping around an already-run contract step), seeds tier versions carrying `capability.case_intelligence` (`python scripts/seed_quota_tiers.py --carry-forward` carries live subscriptions forward), marks the new scripts executable, builds the console and runs the full certification. Rollback: `.\run_arch43.ps1 -Rollback` (code), then `alembic downgrade arch42_step1_entity_graph` (database).
