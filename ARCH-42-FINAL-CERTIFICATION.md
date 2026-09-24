# ARCH-42 — Entity Resolution & Document Knowledge Graph: final certification

**Status: DONE.** `capability.entity_graph`, Business and Enterprise.
**Baseline:** d59baed "ARCH-41 DONE". **Release head:** `arch42_step1_entity_graph`.
**Alembic:** `hm1_tier_price_per_key → arch41_step1_extraction_memory → arch42_step1_entity_graph → arch40_step3_contract_ai_settings` (held; still the only file head).
**Certified:** 2026-09-24, `verify_arch42.py --db --mutate --build --regression` → **52 passed, 0 failed**, on the exact tree `apply_arch42.py` produces.

Everything below was run in a Linux sandbox (PostgreSQL 16.15, pgvector 0.8.1, Python 3.12, Node 22). What was not run is listed in section 7.

## 1. What ARCH-42 delivers

**Database** (`arch42_step1_entity_graph`, expand-only; up → down → up verified):
- Six tables: `entities` (a merge is a pointer, `merged_into_id`, so it can always be undone), `entity_identifiers`, `entity_mentions`, `entity_edges`, `entity_match_models`, `entity_merge_candidates`.
- Every cross-table reference is a composite foreign key on `(id, workspace_id)`, so PostgreSQL itself refuses cross-workspace merges, mentions and edges.
- No plaintext identifier, by constraint:
  - the HMAC column must be 64 hex characters;
  - both ciphertext columns must be Fernet tokens;
  - an Aadhaar row can hold no value (`ck_entity_identifiers_aadhaar_no_value`);
  - a hard identifier (PAN, GSTIN, email, IBAN, passport and the rest) belongs to one record per entity kind (`uq_entity_identifiers_hard_value`).
- The review hub's CHECK and `review_queue_items` view gain a MERGE kind. The view is ARCH-40's text plus one arm.
- The 11 ARCH-38 platform presets gain `x-entity` annotations.

**Engine** (`app/services/entities/`, 13 modules):
- **Normalisation with checksums:** GSTIN, IBAN mod-97, Aadhaar Verhoeff, ISO 6346 containers and the PAN shape.
- **Keyed HMAC per identifier:** looked up under every configured key and re-keyed nightly.
- **Name vectors:** hashed character/token n-gram vectors, because the LIGHT worker profile forbids SentenceTransformers.
- **Candidate generation:** blocking on HMAC, `pg_trgm` and pgvector ANN with iterative scan.
- **Scoring:** Fellegi–Sunter with a per-workspace EM fit.
- **Conflict guard:** a rule, not a weight. It asks the model about everything except the exclusive identifiers.
- **Resolver:** idempotent by `spec_digest`, one advisory lock per workspace.
- **Record operations:** reversible merge, unmerge, split and rename, all audited.
- **Erasure:** removes mentions and every identifier only they supported.
- **Nightly sweep:** re-key, catch up, fit, re-resolve, tidy.

**Wiring:**
- **Capability:** entitlement, 402 display name, and the Business and Enterprise tier seeds.
- **Job:** `entities.resolve_document` on the LIGHT profile, enqueued from `post_enrichment.dispatch_in_session` only for organizations with the plan.
- **Presets:** ENABLED ARCH-38 presets now reach the extraction prompt. A workspace with none gets a byte-identical prompt.
- **Compliance:** ARCH-20 erasure hooks for both document erasure and subject erasure (the subject is found by email HMAC).
- **Preset validation:** preset schemas with a bad `x-entity` block are refused.
- **Review hub:** MERGE resolution (merge or keep separate; "separate" is remembered), hidden without the capability.
- **API:** 13 routes. All are capability-gated except `/potential`.
- **Sweep scheduling:** `scripts/sweep_entities.py`, dispatched by `flowpilot-sweep entities` and scheduled at `47 4 * * *`.

**Console:**
- **Entities page:** search by name or by identifier (matched by HMAC, never stored), kind filters, and a lock card showing how many of the tenant's own documents name parties.
- **Entity 360:** identifiers (masked), document timeline, relationships, graph explorer with depth 1–3, merged records with undo, split, rename, erase (admin), and an obligations placeholder until ARCH-46.
- **Graph explorer:** canvas force layout with no new dependency, draggable nodes and an accessible list of nodes as buttons.
- **Work Item details:** entity chips.
- **Review hub:** an "Entity merges" tab and merge decision panel.
- **Navigation:** a capability-locked nav entry and routes.

## 2. Results

| Gate | Result |
|---|---|
| `verify_arch42.py --db --mutate --build --regression` | **52/52**: 11 offline, 13 database, 25 mutation, 2 build, 1 regression |
| — regression: `verify_arch41.py --db` | 33/33 (automation conformance ALL CONFORMANT, no new triggers) |
| `verify_arch40.py --db` | 45/45 |
| `verify_arch36.py` (chains `verify_arch35`) | 20/20 |
| `verify_hardening_master.py` | 9/9 |
| `verify_hardening_final.py` | 3/3 |
| `scripts/verify_runtime_hardening.py` (G14: every sweep dispatched and scheduled) | 14/14 |
| `tsc -b` + `vite build` | clean (the >1000 kB chunk warning predates ARCH-42) |
| `eslint --max-warnings=0` on the 15 ARCH-42 console files | clean |
| Seeding on an empty database | price book v1 (28 entries), then tiers v1 (unpriced); `--matrix` shows `capability.entity_graph` on Business and Enterprise only |

**Database gates (G1–G12), each on real PostgreSQL inside one rolled-back transaction:**
- **G1:** three spellings of one vendor become one record, and the GSTIN's embedded PAN links the third invoice by identifier.
- **G2:** CVs are joined by email. The Aadhaar number, read from the card's text, is stored as HMAC plus last four only.
- **G3:** same name with a different PAN becomes a HIGH-severity MERGE review with reason `ENTITY_MERGE`, and "keep separate" survives the sweep.
- **G4:** an unchanged document re-resolves to nothing new; a corrected field re-resolves alone.
- **G5:** a bill of lading's graph appears in the graph API.
- **G6:** merge, unmerge and split are reversible and audited; the database refuses cross-workspace merges and plaintext.
- **G7:** no planted identifier appears in any entity table, audit row or outbox event.
- **G8:** EM quality (section 3) and key rotation.
- **G9:** ARCH-20 erasure leaves zero identifiers.
- **G10:** HTTP 402 without the plan and 200 with it; search by PAN; Entity 360; erase.
- **G11:** enrichment dispatch runs only with the plan, and only an ENABLED preset reaches the prompt.
- **G12:** MERGE appears in the review hub only with the plan.

**Mutations:** 25 deliberate breakages, all caught. Anchors are built before the refusal check, so a drifted anchor fails loudly instead of counting as caught. Seven of them break the running system (for example: identifier blocking, the conflict guard, the API gate, Aadhaar storage, erasure, hub resolution, EM anchoring) and must be caught by the named live gate.

## 3. Matching quality

- **Synthetic labelled set** (240 simulated people recorded 1–3 times; 4,003 candidate pairs, 270 true matches):
  - EM converged in 216 iterations.
  - Automatic links: precision 1.00, recall 0.44.
  - Review band: precision 0.862, recall 0.878 (platform defaults: 0.843 / 0.878).
- **Realistic workspace** (G8b): model FITTED and converged (29 iterations, 160 pairs). 28 correct merges, **0 false**.
- **Adversarial workspace** (G8a: no duplicate spelt the same, strangers sharing surnames): the fit was **refused** by the plausibility check and the defaults stayed. 30 correct merges, **0 false**.

**Defect found during certification and fixed.** The first sweep let EM learn every "non-match" probability from the candidate pairs. Because candidates are chosen by name similarity, on the adversarial data EM decided a match meant "shares a name token" and merged strangers: 59 false merges against 6 correct ones in the diagnosis.

The fix:
- Non-match probabilities are fixed from random record pairs for every comparison that blocking does not select on (email, phone, date of birth, exclusive identifiers). Only the name's are learned among the candidates.
- A plausibility check refuses any fit where agreement is not evidence for a match.
- A fit needs at least 30 candidate pairs with something other than a name to compare.

The gate that missed it only counted merges; G8 now checks every merge against ground truth, and mutation MD8 proves it catches the old behaviour.

## 4. `apply_arch42.py`

71 operations: 29 new files and 42 anchored edits. It was generated from `git diff d59baed` plus the new files, and the generator refuses any file without an `ARCH42-S` sentinel. Each edit is an anchor that occurs exactly once, and each result is checked against its final sha256. The apply is atomic and journalled. A file already carrying a later milestone's sentinel (`ARCH43-S1:` onward) is reported as superseded.

| Proof on a clean clone of d59baed | LF checkout | Every text file converted to CRLF (1,459 files) |
|---|---|---|
| `--check` | 71 to write | 71 to write |
| apply | 71 written | 71 written |
| result vs the certified tree (line endings normalised) | 0 differences | 0 differences |
| edited files keep their line endings | yes | 42 / 42 still CRLF |
| second apply | 0 to write, 71 unchanged | 0 to write, 71 unchanged |
| `--rollback` | tree byte-identical to before; backup snapshot removed | tree byte-identical to before; backup snapshot removed |

New files are written with LF. The repository's `.gitattributes` (`* text=auto eol=lf`) checks text files out as LF on Windows too, so CRLF only arises if an editor converted a file; the CRLF proof covers that worst case.

The only path that differed after rollback was `backend/__pycache__/apply_arch42.cpython-312.pyc`, created by the proof harness importing the script (it is git-ignored). ARCH-41's apply engine left its backup snapshot behind after rollback; ARCH-42's engine removes it unless a file was edited after the apply.

## 5. Corrections to the roadmap and handoff (the code disagreed)

1. **ARCH-38 presets never reached extraction.** Before ARCH-42, enabling a preset changed nothing. ARCH-42 annotates the presets in its own migration (ARCH-38's migration is untouched), wires ENABLED presets into the prompt, and adds built-in annotations for the keys the generic prompt already produces.
2. **The Aadhaar/PAN card preset extracts no number, by design.** Numbers are read from the document text with their checksums and stored as HMAC plus last four.
3. **ARCH-32's `token_digest` is not a lookup key.** ARCH-42 has its own HMAC with a key generation and nightly re-keying. Aadhaar cannot be re-keyed (no value is kept); the sweep reports it and fails safe.
4. **The LIGHT worker profile forbids SentenceTransformers**, so name vectors are hashed n-grams (256 dimensions, HNSW).
5. **A sixth table was needed.** A merge proposal found by the sweep is a pair of records, not a mention, so `entity_merge_candidates` is the review hub's source.
6. **PAN uniqueness is per entity kind** (a proprietor's GSTIN embeds their own PAN). A GSTIN contributes its PAN as a derived identifier.
7. **`work_items.extracted_entities` is `json`, not `jsonb`**, and `app.services` re-exports an `llm_service` instance rather than the module.

**Defects in earlier milestones, fixed with `ARCH42-S1:` sentinels:**
- `verify_hardening_master`'s tier matrix had failed since ARCH-41 (it never listed `capability.extraction_memory`).
- `verify_arch40` H1 and A6, and `verify_arch41` E2 and D2, now accept the MERGE kind and the new chain.
- Head pins were widened in `verify_arch31`, `31_step0`, `34`–`39`.

## 6. How to run it (Windows, repository root)

1. Copy `apply_arch42.py` and `verify_arch42.py` into `backend\`, and `run_arch42.ps1` and this file into the root.
2. Run `.\run_arch42.ps1 -CheckOnly`, then `.\run_arch42.ps1` (add `-AllowUnpriced` on a dev database without gateway price ids).
3. To resolve documents processed before the upgrade, run once: `python scripts\sweep_entities.py --apply`.
4. Rollback: `.\run_arch42.ps1 -Rollback`, then optionally `python -m alembic downgrade arch41_step1_extraction_memory`.

## 7. Not verified in the sandbox

- **Windows:** `run_arch42.ps1` itself was not executed. The apply, the verifier and the build ran on Linux.
- **Browser acceptance** of the console. The pages were covered by type-checking, the production build, lint and static gates, not clicked through.
- **A real LLM call** returning preset keys. Prompt construction is verified; the model's output is not.
- **Carrying forward live subscriptions** (the dev database had none), and **real gateway price ids** (seeded unpriced).
- **Performance at production scale** (HNSW and trigram blocking on large workspaces). The nightly sweep caps itself at 20,000 candidate pairs per workspace and kind.

Evidence: `backend/evidence/arch42/verify_arch42.json`.
