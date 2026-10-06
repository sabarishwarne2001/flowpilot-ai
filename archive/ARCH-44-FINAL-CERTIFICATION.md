<!-- ARCH44-S1:docs -->
# ARCH-44 — Complex Table & Hierarchical Grid Extractor: final certification

**Capability:** `capability.table_intelligence` (Business and Enterprise). **Baseline:** e6fbbf1 ("ARCH-43 DONE").
**Release head:** `arch44_step1_table_intelligence`. Chain: `arch42_step1_entity_graph → arch43_step1_case_intelligence → arch44_step1_table_intelligence → arch40_step3_contract_ai_settings` (the held contract step, still the only file head, never run unasked).

Everything below was run in a Linux sandbox (Python 3.12.3, PostgreSQL 16.15, pgvector 0.8.1, Node 22). Only results that actually ran are reported. What could not run there is listed at the end.

## 1. What shipped

| Area | Delivered |
|---|---|
| Token sources | Digital pages: word boxes from the PDF text layer (pypdfium2 loose character boxes; a real space character joins words into one run, a positioning jump does not). Scanned pages: the PaddleOCR blocks already stored in `extraction_metadata` — nothing is re-OCR'd or billed twice. All geometry in 200-DPI page pixels, top-left, the space OCR blocks already use. |
| Rotation and skew | The dominant reading direction is voted from character advances or OCR polygon edges and the page turned a quarter at a time (a /Rotate 90 landscape page, a table printed sideways, an upside-down scan); residual skew is the median OCR top-edge angle, rotated out. The display mapping equals PDFium's `FPDF_PageToDevice` at 0/90/180/270. |
| Candidate grids | **LATTICE**: ruling lines joined into grids (vector paths on digital pages; long dark-pixel runs, numpy only, on scans), cells by rule boundaries, spanning cells where a rule stops short, OCR segments that cross a rule split at the nearest space. **STREAM**: rows and columns by DBSCAN over token geometry (minPts 1, computed exactly by a sweep and proven equal to scikit-learn's DBSCAN), straddling phrases held out of clustering, totals never clustered. **HYBRID**: stream columns with rows banded by horizontal rules when rules separate rows. |
| Hierarchy | Multi-row headers: a label over two or more columns (or centred over the gap between them) is a group ("Amount" › "Debit"/"Credit"); stacked single-column labels join ("Value" + "Dt"). Row kinds BODY, SECTION, SUBTOTAL, TOTAL, CARRY; wrapped narrations fold into their row; indent levels by 1-D DBSCAN with parent sections. |
| Continuation | The last table on page p continues as the first on page p+1 when column signatures match (same columns or a one-to-one positional mapping, types, centres within 6%); a repeated header must read the same and is dropped. |
| Typed values | Indian, Western and European money; Rs./INR/₹/$/€/£; negatives as minus, trailing minus, parentheses, Dr/Cr; identifiers (leading zero, 10+ digits) never numbers; dates day-first/month-first/ISO/named with the order inferred per column. |
| Validation | Relations are discovered, not assumed (≥ 75% of ≥ 3 rows): running balance (debit/credit pair or signed amount), carry-forward, qty × rate, row totals (a ± b, a + b + c, sum of the amounts to the left), column sums on TOTAL rows, subtotals over their section. Failures are localised (a misread balance vs. a misread amount), totals that fail only because of an already-flagged cell are recorded as explained, not flagged again. Passing checks halve a cell's doubt (at most twice); a failure cuts confidence to 40%. |
| Learned column roles | ARCH-41 extraction memory extended: a reviewer's column role is recorded per table layout (header signature, linked to the ARCH-41 template when there is one) and applied to new tables of that layout only when its Wilson lower bound (z = 1.6449) reaches 0.5 with ≥ 3 confirmations. |
| Data (one migration, expand-only) | `extracted_tables` (page range, grid size, header depth, method, rotation, skew, column/row descriptors, status, CHECKs incl. array lengths = grid size), `extracted_table_cells` (row/col/span CHECKs, confidence 0–1 ×3, typed value consistency, flag vocabulary, a trigger refusing cells outside the grid or page range), `table_validations`, `table_column_mappings`; outbox vocabulary gains `trigger.table.flagged`; the review hub gains TABLE. Up / down / up proven. |
| Automation and review | Trigger `table.flagged` (18 triggers over 19 events), emitted once per entry into FLAGGED, in the live conformance matrix. Review-hub kind TABLE (reason TABLE_ARITHMETIC), `review_queue_view_v5()` = ARCH-43's v4 + a TABLE arm, ACCEPT/REJECT through resolution; a table whose figures reconcile after correction leaves the hub by itself. |
| Pipeline | Job `tables.extract_document` on the OCR worker profile, dispatched after enrichment for PDFs and images when the plan carries the capability; inline extraction from the API up to 40 pages, queued beyond; tables a person corrected or decided are kept unless `force`. ARCH-20 erasure deletes a subject's tables. `scripts/backfill_tables.py` queues documents processed before the upgrade. |
| API (9 routes, all gated) | List, detail, export (CSV/XLSX/JSON, audited as EXPORTED), cell corrections, column roles, review, per-document tables, extract, per-document workbook. Reads VIEWER, writes CONTRIBUTOR. |
| Console | Tables list (status filters), table viewer (header spans as detected, confidence heat with legend, failing cells outlined with expected/actual, failures list that scrolls to the cell, double-click or Enter to correct, role selectors, accept/reject, CSV/XLSX/JSON through the authenticated client), "Tables in this document" on Work Item details, locked nav entry, hub tab and resolve panel. |

## 2. Corrections to the handoff prompt and roadmap

1. **Camelot is not used.** It is not in `requirements.txt` (the roadmap said it was). camelot-py 1.0.9 requires `pypdf<6`; the repository pins 6.14.2 and going back reintroduces a fixed pypdf CVE. camelot-py 2.0.0 drops Ghostscript but requires `opencv-python-headless`, which installs the same `cv2` module as the `opencv-contrib-python 4.10` PaddleOCR pins. Its two modes were rebuilt natively (lattice and stream above) with zero new dependencies; pdfplumber was not needed either.
2. **Stored OCR blocks for digital pages are whole lines**, not words (`paddle._extract_pdf` → `pdf_text_layer`), so a row would be one token. Digital pages are read word by word from the PDF text layer; scanned pages use the stored blocks. That is why extraction runs on the OCR profile, not LIGHT.
3. **openpyxl is not in requirements.** XLSX is written with the standard library (`zipfile` + SpreadsheetML); a workbook it writes opens in openpyxl in the sandbox (merged header cells, numbers, dates).
4. **Scope beyond the roadmap line, for the "wire everything" brief:** a fourth table (`table_column_mappings`, which "learn column mappings per layout" requires), a review-hub kind (TABLE) and a Flow Builder trigger (`table.flagged`). The roadmap names neither; both follow the ARCH-42/43 patterns exactly.
5. The prompt's claim that pgvector must be built and apt's NodeSource source disabled held; in this sandbox the default `python3` was 3.11 (scipy 1.18 needs 3.12), so a 3.12 venv was used, and a Docker apt source also had to be skipped.

## 3. Certification run (final code)

`python verify_arch44.py --db --mutate --build --regression` → **74 passed, 0 failed** (19 offline, 9 database, 43 mutations, 2 build, 1 regression), 133 s. Evidence: `backend/evidence/arch44/verify_arch44.json`.

**Golden files and held-out documents (all synthetic, exact truth).** 14 golden documents (`tests/fixtures/arch44_golden.json`, 2,816 cells): a 3-page borderless bank statement with two-line headers, wrapped narrations, a sparse cheque column and a TOTAL row; a 2-page scanned statement as stored OCR blocks, skewed +1.2° / −0.9°, with carried/brought-forward rows; a two-level-header ledger; nested line items (sections, indented items, subtotals, grand total); a clinical chart with a two-level header and text ranges; a ruled invoice (digital) and the same invoice as a scan whose OCR merged adjacent cells; a /Rotate 90 landscape register and the same table printed sideways; planted-error variants. **Every cell, header path, row kind, level, page range, method and flagged cell is exact.** On 140 held-out documents (seeds 101–110, every generator, clean and planted; 28,020 cells) the result is the same: all exact. The generator's truth is byte-compared with the golden file, and the PDFs are byte-identical to the ones the golden file was made from.

What each stage is worth, measured: the scanned ruled invoice is 100% with lattice and **36.7%** with stream alone; the 1.2° scan is 100% with deskew and **0%** without; the 3-page statement is one table with continuation and three without; the sideways table reads correctly only with the reading-direction vote (it reports rotation 270).

**Validator.** Planted errors are flagged exactly — 9 cells across 5 documents, none on any clean document: a misread withdrawal (localised to the amount), a misread balance (localised to the balance), a wrong deposit total, a misread debit and a wrong brought-forward balance on the scan (CARRY_FORWARD), a misread credit, a misread line amount whose subtotal and grand total are then *explained* rather than flagged, a wrong subtotal, a misread ruled-invoice amount. Relations found: statement RUNNING_BALANCE + COLUMN_SUM; ledger ROW_PRODUCT + HIERARCHY_SUM + COLUMN_SUM; invoice ROW_PRODUCT + COLUMN_SUM; clinical none (EXTRACTED, not VALIDATED).

**Database layer** (one rolled-back transaction, HTTP where it matters): zero column-level drift between models and migration; 16 schema refusals (grid and page trigger, spans, confidence/type/value/flag CHECKs, cross-workspace FK, FLAGGED without failures, REVIEWED without a time, array length, layout key, validation shape, unknown role); extraction through the service with the stored grid equal to the truth for a digital and a scanned document; FLAGGED at the planted cells, `table.flagged` exactly once and not again on re-validation, hub item OPEN; HTTP 402 on all 9 routes with the denial audit, 200s with the plan, exports audited, 422s, corrections → VALIDATED and hub RESOLVED, re-extract 409 then `force`, review 200 then 409; learned mapping not applied after two confirmations and applied (LEARNED) after three; hub ACCEPT through `resolution.resolve_item`; the job enqueued and run only with the plan (LATTICE); ARCH-20 erasure leaving no table or cell.

**Mutations (43, all caught):** 9 live (API gate removed, trigger re-emitted on every re-validation, hub cannot resolve TABLE, corrections not re-validated, learned mappings never applied, erasure keeps tables, corrections overwritten without force, dispatch ignores the plan, validation disabled) and 34 static (capability packaging ×3, grid trigger dropped, confidence CHECK dropped, two heads, column eps ×8, figures merged, continuation off, reading direction off, deskew off, lattice off, gap-centred group labels, TOTAL by prefix, balance localisation removed, echoes flagged, tolerance 1,000, confidence inverted, Indian grouping, day/month inference, CSV injection guard, XLSX merges, Wilson bypassed, raster threshold fixed, LIGHT profile, dispatch ungated, erasure hook, hub ungated, API route ungated, write open to viewers, console type drift, exports without the session, resolve panel without TABLE, trigger emitter removed). Every mutation's anchor is built before the refusal check.

**Build:** `tsc -b` + `vite build` clean; `eslint --max-warnings=0` clean on all 14 ARCH-44 console files.

## 4. Packaging proof

`backend/apply_arch44.py`: **72 operations** (30 new files, 42 edits through 100 anchors), generated from `git diff e6fbbf1`; the generator refuses any file without an `ARCH44-S` sentinel and replays every edit against the baseline text to the final sha256. Evidence JSON is not packaged (each run writes its own).

| Clean tree of e6fbbf1 | `--check` | apply | tree = build | 2nd apply | `--rollback` |
|---|---|---|---|---|---|
| LF | writes nothing | 72 written | all 72 identical | 0 writes, 72 "already applied" | byte-identical over 1,565 files, `git status` clean, backup removed |
| CRLF (every text file rewritten CRLF) | writes nothing | 72 written | all 72 identical | 0 writes | byte-identical over 1,565 files; 42/42 edited files kept CRLF |

An applied LF tree passes `verify_arch44.py` offline (19/19).

## 5. Regression (final code)

`verify_arch43 --db` 27/27 · `verify_arch42 --db` 24/24 · `verify_arch41 --db` 33/33 · live automation conformance **ALL CONFORMANT** (18 triggers + 7 actions; `table.flagged` COMPLETED, in the timeline, and refused with 422 without the plan) · `verify_arch40 --db` 45/45 · `verify_arch39 --db` ✔ · `verify_arch37 --db` ✔ · `verify_arch36 --db` ✔ · `verify_arch35 --db` ✔ · `verify_arch34 --db` 110/110 · `verify_arch31 --db` ✔ · `verify_arch31_step0 --db` ✔ · `verify_hardening_master` 9/9 offline · `scripts/verify_runtime_hardening.py` 14/14 · `verify_arch38 --db` all gates except A1 (below).

## 6. Found and fixed during the build

- **"1,000" read as one-point-zero.** The European-decimal rule accepted any comma followed by digits; a Western thousands group is now never a decimal comma (caught by gate G3).
- **Totals bridging columns.** A statement's TOTAL row prints wider figures than its rows, wide enough to chain two right-aligned columns in DBSCAN; summary rows are now placed, never clustered (found by the seed sweep; mutation MS8 guards the related rule that two figures side by side are never one cell).
- **"Total WBC Count" became a total row.** TOTAL detection now requires the label to *be* a total (MS14).
- **Tight columns.** Gap thresholds alone either glued adjacent columns or split words; the text layer's real space characters now decide, with a tight gap rule as the fallback.
- **Hairline rules vanished on scans.** A 0.6 pt rule rendered at 100 DPI anti-aliases to mid-grey; darkness is now relative to the paper (MS24).
- **Two harness mutations that did nothing.** Patching `TOL` had no effect because `close()` binds it as a default argument, and patching `geometry.rules_from_bitmap` missed the name the reader imported. Both were re-aimed at the bindings the code uses, and both are then caught.
- **Earlier verifiers pinned to ARCH-43's shape**, widened with `ARCH44-S1:` sentinels, never replaced: head pins (31, 31_step0, 34–43), chains (40 A8, 41, 42, 43 T2, hardening master), `verify_arch37` emitter/later/counts, `verify_arch38` B2, `verify_arch40` A6/H1/T1, `verify_arch41` catalog, `verify_arch42` E8/vocabulary, `verify_arch43` vocabulary/outbox/catalog/conformance/M16, `verify_arch36` GATED_PAGES, hardening matrix, `automation_conformance` (18).

## 7. Pre-existing failures (not ARCH-44; each reproduced on a clean e6fbbf1 worktree with its own database)

- `verify_arch38` **A1** — `apply_arch38.py` refuses `preset_service.py`, which ARCH-42 edited after ARCH-38 created it.
- `verify_hardening_master --db` **D1, D5, D6, D7** (16/20) and `verify_hardening_final --db` **"Payment loop (Stripe)"** (4/5): tier prices, Dodo/Stripe webhooks, dunning and seat sync need real gateway price ids and webhook secrets, which the sandbox does not have. Identical on the baseline.
- `scripts/verify_arch10_step3.py` / `verify_arch10_step9.py` — not run (the ARCH-43 hazard: never against a database you back up).

## 8. Not run in the sandbox

- `run_arch44.ps1` on Windows (its check, apply, migrate, seed, build and verify steps ran as their Linux equivalents; the `git update-index --chmod=+x` step did not).
- Clicking through the console in a browser: the pages are type-checked, built and linted and their API contracts exercised over HTTP, but no UI interaction was driven.
- **Real documents and real PaddleOCR output.** Every accuracy figure above is on synthetic documents with exact truth (digital Courier PDFs and simulated OCR blocks). Accuracy on real bank statements, proportional fonts, handwriting, low-quality scans and PaddleOCR's own segmentation is **unmeasured**. The reviewer corrections and learned column roles exist for exactly that gap, and every correction is recorded.
- Performance at the 500-page limit and a concurrent worker fleet (the 3-page statement extracts in about 0.25 s).
- XLSX in Microsoft Excel itself (validated structurally and by openpyxl only).

## 9. Deploying

From the repository root: `.\run_arch44.ps1` (add `-AllowUnpriced` on a development database). It checks, applies, migrates to `arch44_step1_table_intelligence` (stamping around an already-run contract step), seeds tier versions carrying `capability.table_intelligence` (`--carry-forward` moves live subscriptions), builds the console and runs the full certification. Then, once: `python scripts\backfill_tables.py --apply` to extract tables from documents processed before the upgrade. Rollback: `.\run_arch44.ps1 -Rollback` (code), then `alembic downgrade arch43_step1_case_intelligence` (database).
