<!-- ARCH41-S3:docs -->
<!-- ARCH42-S1:docs -->
# FlowPilot AI — Master Roadmap, ARCH-41 through ARCH-50

<!-- ARCH43-S1:roadmap-status -->
<!-- ARCH44-S1:roadmap-status -->
<!-- ARCH45-S1:roadmap-status -->
**Status:** ARCH-41 through ARCH-45 delivered and certified (see `ARCH-41-FINAL-CERTIFICATION.md` through `ARCH-45-FINAL-CERTIFICATION.md`). ARCH-46 to ARCH-50 specified here.
**Alembic after ARCH-45:** `hm1_tier_price_per_key → arch41_step1_extraction_memory → arch42_step1_entity_graph → arch43_step1_case_intelligence → arch44_step1_table_intelligence → arch45_step1_corroboration → arch40_step3_contract_ai_settings` (held). One file head; the release head is `arch45_step1_corroboration`. (This line still described ARCH-42's chain until ARCH-45 corrected it.)
**Constraint held throughout:** no new recurring external cost. FastAPI, PostgreSQL 16 + pgvector ≥ 0.8, Redis, MinIO, PaddleOCR, SentenceTransformers, SciPy, scikit-learn, pikepdf, pypdfium2 (camelot was never in requirements; ARCH-44 builds lattice and stream grids natively), React 19, Vite, Tailwind.

This document adopts the harmonized ten-milestone plan (governance and intelligence engines fused with the commercial pillars: packet splitting, table extraction, N-way corroboration, real-time collaboration), with four adjustments recorded in §2.

---

## 1. The ten milestones

| # | Milestone | Capability key | Tier | Depends on |
|---|---|---|---|---|
| 41 | Operational Seams, Automation Conformance & **Extraction Memory** — DONE | `capability.extraction_memory` | Business+ | — |
| 42 | Entity Resolution & Document Knowledge Graph — DONE | `capability.entity_graph` | Business+ | 41 |
| 43 | Universal Packet Dicer & Case Intelligence (+ per-tenant fair queuing) — DONE | `capability.case_intelligence` | Business+ | 42 |
| 44 | Complex Table & Hierarchical Grid Extractor — DONE | `capability.table_intelligence` | Business+ | 41 |
| 45 | Universal Document Corroborator & Discrepancy Matrix — DONE | `capability.universal_corroborator` | Enterprise | 42, 44 |
| 46 | Obligations & Temporal Intelligence | `capability.obligations` | Business+ | 42 |
| 47 | ERP & System-of-Record Posting | `capability.erp_posting` | Business+ | 42, 44 |
| 48 | Real-Time Collaborative Review & Live Presence | `capability.collaborative_review` | Enterprise | 40 hub |
| 49 | Process Intelligence & Governed Exception Agent | `capability.process_intelligence` | Enterprise | 43–47 |
| 50 | Sovereign Edition, RevOps, DR Certification & GA-2 | `capability.egress_lockdown` + platform | Enterprise / Platform | all |

**Monetization after ARCH-50.** Free: core extraction and chat. Developer ($49): + API, webhooks, branding, vanity domain. Business ($299): + matching, radar, custom email, warehouse egress, extraction memory, entity graph, case intelligence and packet dicer, table intelligence, obligations, ERP posting. Enterprise ($799): everything, adding calibrated autonomy, redaction, clause assertions, SSO/SCIM, priority SLO, corroborator, collaborative review, process intelligence and the agent, egress lockdown and the sovereign edition licence.

## 2. Adjustments to the harmonized plan, and why

1. **Disaster recovery did not disappear.** The harmonized list dropped the earlier resilience milestone. ARCH-41 shipped the backup floor; point-in-time recovery (WAL-G to MinIO plus off-host copy), chaos gates and measured RPO/RTO become a **mandatory GA-2 gate inside ARCH-50**. A platform that sells a 99.9% SLO cannot reach general availability without them.
2. **Fair queuing moves to ARCH-43.** The packet dicer is the first feature that turns one upload into a hundred jobs. Weighted per-organization claiming in `workers/claim.py` ships with it, so one tenant's 400-page scan cannot starve every other tenant's OCR.
3. **One key per milestone.** The exception agent rides `capability.process_intelligence` rather than a second key (the "ONE KEY" rule ARCH-34 recorded): a tenant cannot hold the agent without the mining that feeds it.
4. **Entities before dicing, with re-resolution.** ARCH-42 resolves mentions per work item. When ARCH-43 splits a packet, child documents get their own mentions and the parent's are superseded through lineage, so nothing learned in ARCH-42 is lost.

## 3. Conventions every milestone follows (learned the hard way in ARCH-41)

- **Capability registration is six places, not four:** `core/entitlements.py` (constant, `CAPABILITY_KEYS`, an `Entitlement` entry — without it `has_capability` raises), `api/capability_gate._DISPLAY_NAMES`, `scripts/seed_quota_tiers.py` packaging, frontend `constants/capabilities.ts`, `constants/planFeatures.ts` (typed `Record` — tsc fails without it — AND `PLAN_FEATURE_ORDER`, the only keys a plan card renders; ARCH-45 added the four ARCH-41–44 keys that had been missing), and `verify_arch36.GATED_PAGES` for a capability-locked nav entry. Then `seed_quota_tiers.py --carry-forward` so live subscriptions receive it.
- **Gating is imperative:** `capability_gate.require_capability(db, context=..., capability_key=..., operation=...)` → 402 `CAPABILITY_REQUIRED`. No decorator exists.
- **Migration position:** new expand-only revisions insert BEFORE the held contract step `arch40_step3_contract_ai_settings`, re-parenting it, as hm1 and ARCH-41 did; the run script handles a database whose contract already ran (stamp under, upgrade, stamp back).
- **Head pins:** widen `verify_arch31, 31_step0, 34, 35, 36, 37, 38, 39, 40` and `verify_hardening_master` with an `ARCHnn-S1:head-widened-xx` sentinel; never replace an earlier sentinel.
- **`work_items` has no `organization_id` column.** Resolve it through the workspace (`extraction_memory.gate.organization_of`). ARCH-41's live gate caught this.
- **Audit outcomes are `ALLOWED` / `DENIED`**, not `SUCCESS`. Calibration's model class is `CalibrationModelVersion`.
- **Packaging:** `apply_archNN.py` with sha-guarded `new` and `edit` operations (edits are anchored hunks with a final-hash check), `verify_archNN.py` (offline / `--db` / `--mutate` / `--build` / `--regression`), `run_archNN.ps1`.

---

## 4. Milestone specifications

### ARCH-41 — Operational Seams, Automation Conformance & Extraction Memory (delivered)

Redaction Studio authenticated previews, back navigation and a hardened drawing canvas; the live automation conformance matrix (14 triggers and 7 actions, authored through HTTP, emitted through the real outbox, executed by the real handler, read back through the timeline routes); an encrypted backup floor with a weekly restore drill; and Extraction Memory — reviewed corrections harvested into encrypted per-layout exemplars and Wilson-bounded anchor rules, applied only after a randomized trial proves fewer corrections (one-sided Mann–Whitney U, per-field non-inferiority), with conformal autonomy held until recalibration. Details and evidence: `ARCH-41-FINAL-CERTIFICATION.md`.

### ARCH-42 — Entity Resolution & Document Knowledge Graph (`capability.entity_graph`, Business+) — delivered

**As built (see `ARCH-42-FINAL-CERTIFICATION.md`).** Six tables, not five: `entity_merge_candidates` is the review hub's MERGE source (a guard refusal found by the nightly sweep is a pair of records, not a mention). ARCH-38 presets now reach the extraction prompt when ENABLED (before ARCH-42 enabling a preset changed nothing); a built-in annotation map covers the generic prompt's keys. Name vectors are hashed character/token n-grams (the LIGHT worker profile forbids SentenceTransformers). Identifier HMACs are looked up under every configured key and re-keyed nightly. PAN uniqueness is per entity kind (a proprietor's GSTIN embeds their own PAN); a GSTIN contributes its PAN as a derived identifier. The conflict guard asks the model about everything EXCEPT the exclusive identifiers. The Aadhaar/PAN card preset still extracts no number; numbers are read from the text with their checksums and stored as HMAC + last four.

**Original specification:**

**Objective.** One canonical record per person, organization, address, account, asset or shipment across every document, with the relationships between them.
**Why it sells.** "Show me every document and obligation tied to Vendor X or Patient Y" — the question buyers ask in the first demo; it is also the join key ARCH-43, 45, 46 and 47 need.
**Backend.** `services/entities/`: mentions from extracted fields via `x-entity` annotations on preset schemas (the 11 ARCH-38 presets gain them); blocking on HMAC-hashed hard identifiers (email, PAN, GSTIN, IBAN, passport), `pg_trgm` name similarity and pgvector ANN over name embeddings; Fellegi–Sunter match weights fitted by EM (numpy/scipy); clustering with a conflict guard (a merge that would join two clusters holding different hard identifiers goes to review); reversible, audited merge and split; incremental worker on `work_item.enriched` (enrich profile) and a nightly re-resolution sweep (`scripts/sweep_entities.py`, dispatched and scheduled per RH-4 G14). Aadhaar numbers stored as HMAC + last four only (confirm with counsel).
**Data.** `entities` (kind CHECK, status CHECK, composite self-FK `merged_into_id`), `entity_identifiers` (HMAC + encrypted display, partial UNIQUE per workspace/kind/hmac), `entity_mentions` (composite FK to `work_items (id, workspace_id)`, decision CHECK AUTO/REVIEW/CONFIRMED/REJECTED), `entity_edges` (UNIQUE src/dst/relation, evidence work item), `entity_match_models` (per-workspace EM parameters, versioned). Erasure (ARCH-20) cascades identifiers and mentions.
**Frontend.** Entities list; Entity 360 (documents timeline, identifiers, relationships, obligations placeholder); graph explorer (canvas force layout, no new dependency); a `MERGE` review kind in the ARCH-40 hub; entity chips on Work Item details.
**Gates.** EM convergence and precision/recall floors on a labelled synthetic set; no plaintext identifier in any table; cross-workspace merge refused by FK; erasure leaves zero identifiers; conflict guard routes to review; mutations on normalisation, blocking, the guard and the capability gate; `--db` end to end with HTTP 402/200.

### ARCH-43 — Universal Packet Dicer & Case Intelligence (`capability.case_intelligence`, Business+) — delivered

**Objective.** Split 100+ page scanned bundles into child documents with lineage, then assemble documents into cases with completeness and cross-document consistency checks.
**Why it sells.** Chaotic scanned bundles are the first intake problem every enterprise has.
**Backend.** Boundary detection per page from signals already computed (OCR header skeletons via ARCH-41 fingerprints, page-number resets, blank/separator pages, classifier changes) scored by a logistic model (scikit-learn) with a reviewer-correctable split plan; children created as work items with `parent_work_item_id` and page ranges (pikepdf split, originals retained); case templates with required document types and a small consistency DSL (`EQUAL`, `FUZZY_EQUAL`, `DATE_ORDER`, `WITHIN_DAYS`, `SUM_EQUALS`); assembly by entity (ARCH-42) and batch (ARCH-38); missing-document requests with single-use expiring upload tokens; **weighted per-organization fair claiming** in `workers/claim.py`.
**Data.** `packet_splits`, `packet_split_segments` (page range CHECKs, no overlaps via exclusion constraint), `case_templates` (immutable once published), `cases`, `case_documents`, `case_rule_results`, `document_requests`, `tenant_queue_weights`.
**Frontend.** Split review (page thumbnails with draggable boundaries, using ARCH-41's authorized blob hook), case board, case detail checklist and consistency matrix.
**Gates.** Boundary precision/recall on synthetic packets; lineage integrity; token single use; fairness bound under a 10,000-job flood; new triggers `packet.split`, `case.completed`, `case.inconsistent` in the ARCH-41 live conformance matrix.
**As built.** One migration, `arch43_step1_case_intelligence` (the contract step re-parented onto it). Fair claiming is the default for every worker loop; the build also found and fixed a pre-existing over-claim in `claim_eligible_rows` (an `UPDATE … IN (LIMIT subquery)` could claim more than the batch). Split plans are a fifth review-hub kind, SPLIT. Children reuse the parent's per-page OCR, so a split is never OCR'd or billed twice. The recipient upload is the first ARCH-4x public route (`PUBLIC_ROUTES`). Held-out boundary quality: precision 0.975, recall 0.898 (page-number heuristic F1 0.714).

### ARCH-44 — Complex Table & Hierarchical Grid Extractor (`capability.table_intelligence`, Business+) — delivered

**Objective.** Borderless, multi-page, rotated tables (ledgers, statements, clinical charts, nested line items) into typed JSON/CSV with cell-level confidence.
**Backend.** Candidate grids from camelot (lattice and stream) and PaddleOCR token boxes; row/column clustering by DBSCAN over token geometry; header hierarchy detection; continuation across pages by column-signature matching; arithmetic validation (row totals, column sums) raising cell confidence or flagging; extraction memory (ARCH-41) extended to learn column mappings per layout.
**Data.** `extracted_tables`, `extracted_table_cells` (row/col CHECKs, confidence 0–1), `table_validations`.
**Frontend.** Table viewer with cell confidence heat, validation failures inline, CSV/XLSX export.
**Gates.** Golden-file accuracy on synthetic statements; arithmetic validator catches planted errors; multi-page continuation.
**As built.** One migration, `arch44_step1_table_intelligence` (the contract step re-parented onto it), adding a fourth table, `table_column_mappings`, for the learned column roles. **Camelot was not used:** it is not in `requirements.txt` (this roadmap said it was), 1.0.9 requires `pypdf<6` (the repository pins 6.14.2, and 5.x reintroduces a fixed CVE), and 2.0.0 requires `opencv-python-headless`, which installs the same `cv2` module as the `opencv-contrib-python` PaddleOCR pins. Its two modes were rebuilt natively: LATTICE from vector ruling lines (pypdfium2) or, on scans, dark-pixel runs (numpy), and STREAM from DBSCAN over token geometry, computed exactly by an interval sweep and proven equal to scikit-learn's DBSCAN. Word boxes come from the PDF text layer (the stored blocks for digital pages are whole lines); scanned pages use the OCR blocks already stored, so nothing is re-OCR'd. Rotated (/Rotate and sideways-printed) and skewed tables are normalised first. The validator discovers relations (running balance, qty × rate, row totals, column sums, subtotals, carry-forward) and localises failures; flagged tables are a sixth review-hub kind (TABLE) and a Flow Builder trigger (`table.flagged`, 18 triggers). Accuracy: every cell exact on 14 golden documents (2,816 cells) and on 140 held-out documents (28,020 cells, seeds 101–110); synthetic documents only — real-world accuracy is unmeasured.

### ARCH-45 — Universal Document Corroborator & Discrepancy Matrix (`capability.universal_corroborator`, Enterprise) — delivered

**Objective.** Compare any 2–5 documents (contract vs. amendment, policy vs. claim, spec vs. delivery) with side-by-side visual diffs and a clause/field discrepancy matrix.
**Backend.** Alignment in three layers — fields (extracted values), entities (ARCH-42 canonical ids), clauses (sentence embeddings with the existing SentenceTransformer, Hungarian assignment via SciPy); materiality scoring; ARCH-33 assertions reused as corroboration rules; results cached per document-set hash.
**Data.** `corroboration_runs`, `corroboration_pairs`, `discrepancies` (kind, materiality, evidence spans).
**Frontend.** N-pane synchronized viewer, discrepancy matrix, export to PDF report.
**Gates.** Planted-difference recall; order independence; cache invalidation on reprocessing.
**As built.** One migration, `arch45_step1_corroboration` (the contract step re-parented onto it), adding a fourth table, `corroboration_documents` (the ordered members of a run and the content hash each was compared at; deleting a document deletes its comparisons). Five layers, not three: FIELD (extracted values by concept — money within a tolerance, identifiers compared alphanumerically, parties by ARCH-42 entity id when both are resolved), ENTITY (live ARCH-42 mentions followed through `merged_into_id` to their root), CLAUSE (clauses segmented from the stored page lines with running headers/footers, page numbers, "Label: value" lines and ARCH-44 table regions removed; encoded with the platform's SentenceTransformer on the ENRICH profile — a hashed lexical encoder where the model cannot load — blended with a token Dice score, assigned pairwise with SciPy's Hungarian solver, re-paragraphed clauses absorbed, then grouped N-way with at most one unit per document), TABLE (ARCH-44 line items through `tables.service.load`, aligned by code and description, compared on quantity, unit price and amount; tax and total rows are not line items) and RULE (ARCH-33 plans — the workspace's saved deterministic assertion definitions and sentences given with the comparison — evaluated on every document's clauses; the llm family is refused, so no model is called and nothing is billed). Materiality is clause importance × the kind of change: a changed value or a flipped obligation is material, rewording never (severity bands 0.75 / 0.5). Documents are processed in a canonical order, so every permutation gives the identical result. The cache key is a fingerprint of the engine, encoder, options, rule digests and every document's content hash (text, page boxes, fields, table revisions, entity roots): reprocessing, a table re-extraction or correction and the nightly sweep mark changed comparisons STALE, and a newer answer for the same documents supersedes the older. Comparisons with material differences are a seventh review-hub kind (CORROBORATION, reason MATERIAL_DISCREPANCY) and a Flow Builder trigger (`corroboration.discrepancies`, 19 triggers over 20 events). The PDF report is drawn with pikepdf and Helvetica's metrics (no reportlab). Planted-difference recall and precision are 100% on 5 golden sets (22 planted) and 120 held-out sets (seeds 201–260, 769 planted) — synthetic documents only; real-world accuracy is unmeasured, and in the sandbox the SentenceTransformer path ran with a stand-in model (the MiniLM weights could not be downloaded).

### ARCH-46 — Obligations & Temporal Intelligence (`capability.obligations`, Business+)

Deterministic date arithmetic (notice = renewal − notice period; business days from stored holiday calendars; RRULE recurrence), obligation records with owners and states, nightly sweep emitting `obligation.due_soon` / `obligation.overdue`, signed revocable iCal feeds. Tables `obligations`, `obligation_events`, `calendar_feed_tokens`, `holiday_calendars`. Calendar, list and Entity-360 tab. Gates: leap years, month ends, timezones, sweep idempotency, feed revocation.

### ARCH-47 — ERP & System-of-Record Posting (`capability.erp_posting`, Business+)

Exactly-once posting of approved outcomes: canonical posting objects; a safe mapping language; adapters for CSV/XLSX, EDI X12 810/850/856, UBL 2.1, Tally Prime XML, SFTP, generic REST/OData, with presets for QuickBooks Online, Zoho Books, Business Central, S/4HANA OData and NetSuite REST; idempotency ledger; acknowledgement reconciliation; `erp.post` action and `posting.failed` trigger (added to the conformance matrix). Verified against published contracts, golden files and mock servers — live ERPs need customer sandboxes.

### ARCH-48 — Real-Time Collaborative Review & Live Presence (`capability.collaborative_review`, Enterprise)

**Backend.** FastAPI WebSockets authenticated with the existing session; Redis pub/sub for fan-out across workers (Redis already runs); per-item soft locks with heartbeats and expiry; presence; paragraph-anchored discussion threads; optimistic concurrency on resolutions (version column) so two reviewers can never both resolve one item.
**Data.** `review_locks`, `review_threads`, `review_comments`.
**Frontend.** Live queue updates, avatars on items, lock badges, thread panel.
**Gates.** Two-client race tests; lock expiry; fan-out across two worker processes; Caddy WebSocket pass-through.

### ARCH-49 — Process Intelligence & Governed Exception Agent (`capability.process_intelligence`, Enterprise)

Object-centric event log from the outbox, audit log, jobs, reviews, executions, cases and postings; directly-follows discovery, variants, conformance token replay against ARCH-37 flows and ARCH-43 templates; SLA-breach prediction (gradient boosting, Brier-checked); cost-to-serve from ARCH-18/24. The agent works review items, inconsistent cases, failed postings and radar findings through a typed tool registry, proposes resolutions, never applies without approval except where ARCH-35 conformal bounds exist for the proposal kind; document text is always fenced; injection corpus in the gates.

### ARCH-50 — Sovereign Edition, RevOps, DR Certification & GA-2 (`capability.egress_lockdown` + platform)

One egress gate for every outbound client with tenant allowlists and a deployment-wide deny mode; operator-configured local LLM (llama.cpp / vLLM / Ollama, OpenAI-compatible, never tenant-supplied); Ed25519 offline licence; SBOM and signed checksums; annual intervals, INR price books, promo codes, invoiced enterprise contracts, revenue metrics; **PITR, chaos gates and measured RPO/RTO**; GA-2 = the full verify chain 41–50 plus hardening on a clean database.

---

## 5. Handoffs

ARCH-42 was started from `ARCH-42-HANDOFF-PROMPT.md`, ARCH-43 from `ARCH-43-HANDOFF-PROMPT.md`, ARCH-44 from `ARCH-44-HANDOFF-PROMPT.md` and ARCH-45 from `ARCH-45-HANDOFF-PROMPT.md`. The self-contained prompt for ARCH-46 is in `ARCH-46-HANDOFF-PROMPT.md`.
