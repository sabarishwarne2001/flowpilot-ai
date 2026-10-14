# Next session prompt — FlowPilot AI hardening (after campaign session 4)

Paste everything below the line into a new Claude Code session on this repository.

---

You are continuing the FlowPilot AI pre-launch hardening campaign. Session 4 ended with pull request
`sabarishwarne2001/flowpilot-ai#23` (branch `claude/kind-hamilton-5lpp19`). If that pull request is
merged, start from the latest `main`; if it is still open, continue on its branch.

**Who you work for.** A solo founder who is new to SaaS. Explain everything in plain language. You
have founder authority for product decisions inside this list: decide like a careful Tier-1 SaaS,
build it, and record each decision in `docs/hardening/NEEDS-OWNER.md` as
`DECIDED (campaign session 5)` (next number N-078). Never ask; never stop to wait.

**Read first, in this order:** `CLAUDE.md` (hard rules — they win over this file),
`docs/hardening/STATE.md`, `docs/hardening/HANDOFF.md` ("Session 4 → later sessions" and the
"Owner launch checklist"), `docs/hardening/FINDINGS.md` (the section "Session 4 (continued) —
reproduced or read in code, NOT proven"), `docs/hardening/NEEDS-OWNER.md` N-074 to N-077,
`docs/PRODUCT-NEXT.md`.

**Rules that matter most.**
- Evidence rule: a bug is fixed only when a test failed first, the fix is applied, that test passes
  and the rest of the suite still passes. Anything not proven is reported as "unverified".
- One logical fix per commit; the message says what and why. New findings start at **F-410**.
- Never weaken, skip or delete a test. Never commit secrets, `.env` files or binaries. Exactly one
  Alembic head; every migration must go up, down and up again.
- Do not read or edit the historical scripts listed in `CLAUDE.md` (`apply_*.py`, `verify_*.py`
  except `backend/scripts/verify_arch14.py`/`verify_arch15.py`, `backend/evidence/`, `archive/`, …).
- **Usage:** run at most two helper agents at a time, give each a narrow brief with a step budget,
  and prefer doing small fixes yourself. Run long suites in the background. Keep outputs short.
- After 3 failed attempts on one bug, record it in `docs/hardening/BLOCKERS.md` and move on.
- Update `STATE.md` before stopping; keep `campaign_slices.py --check all` at exit 0 (add a ledger
  row for every new endpoint or migration).

**Local environment.** `frontend/e2e/README.md` explains the stack (Postgres with pgvector on 5433,
Redis, the API, the worker, the mail catcher and the model stand-in; `E2E_LLM=1`). Browser tests:
`cd frontend && npx playwright test -c e2e tests/<file>.spec.ts`. Backend tests need Postgres too
(`backend/tests/conftest.py`; use a separate `TEST_DB_NAME` per parallel run). Never create
`backend/.env` in the repository for tests; export the variables in the shell instead.

## Tasks, in priority order

### 1. Prove and fix the audit's unverified defects (each: failing test first)
**Batch operations** (`backend/app/services/batches/`, tests in
`backend/tests/engines/test_batch_hardening_live.py`):
1. Two dispatches of one batch at once, or two batches sharing a document → 500 on the dispatch
   tag's unique key. Fix: insert tags with `ON CONFLICT DO NOTHING`.
2. Adding the same document to a batch twice at once → 500, and simultaneous adds can pass the
   2,000-document limit. Fix: lock the batch row in `add_documents`.
3. A batch whose documents were erased (GDPR erasure) → the package build fails for the whole
   batch, or includes an original whose delete failed. Fix: skip originals whose upload is deleted.
4. N-076: an extra file outside the package folder, or a duplicated entry name, still verifies.
   Make any extra file "Tampered" except `__MACOSX/…` and `.DS_Store`.
5. The batch list reads every organization's `document_verifications` before joining (scope it to
   the workspace); archived batches can still be dispatched, healed and edited (refuse with 409);
   downloading an expired package runs the global expiry sweep inside the request.
6. UI (`frontend/src/pages/batches/`): "Active batches" counts only the loaded page and keeps its
   label on the Archived tab; straight-through and confidence tiles use the current page only;
   Delete is not disabled while another action runs; the verify table's row key can repeat.

**TruthMesh** (`backend/app/services/truthmesh/`, `backend/tests/engines/test_truthmesh_hardening_live.py`):
1. Deleting a document leaves its conflicts OPEN (and in "money at risk") until a rebuild — single
   delete, bulk delete and the retention purge (`scripts/sweep_compliance.py`).
2. The reason typed for a conflict decision is not in the audit row, and reopening erases it
   (`service.decide_conflict` sets `resolution_note` to None).
3. A rebuild while a document is reprocessing or failed can delete a person's confirmed or rejected
   link (`_remove_missing`); the rejected link comes back as automatic.
4. The written-but-not-run test that a dismissal made during a rebuild is not overwritten by
   "auto-resolved" (F-382's lock should already prevent it — prove it).
5. Small: a huge `offset` on `/truthmesh/conflicts` is a 500 (cap it, `le=` on the Query, and look
   for the same pattern on other list endpoints); simulating an unknown document answers 409 instead
   of 404; rebuild requests are not coalesced; overview/graph/matrix/export load every node with its
   embedding (measure at 5,000 documents, then select only needed columns).
6. UI (`frontend/src/pages/truthmesh/TruthMesh.tsx`, `ConflictCard.tsx`): the conflict list stops
   at 100 with no "load more"; the what-if picker only offers the graph's first 400 documents and the
   "truncated" flag is never shown; a cleared days or percent box sends 0 (422 shown as a generic
   toast); the reason box has no 2,000-character limit; Rebuild stays disabled forever if a build is
   stuck in BUILDING.

**Document packs** (`backend/app/services/ingestion/preset_service.py`, `services/entities/presets.py`):
1. F-400: a real PAN card contains only one of the "Aadhaar / PAN card" pack's phrases, so enabling
   the pack never changes PAN extraction. Add a data migration with a second phrase ("income tax
   department"), update the `platform_presets` test fixture, restore the PAN row in the `DOCUMENTS`
   table of `backend/tests/engines/test_document_packs_live.py`.
2. Phrase matching is a plain substring ("lease" matches "pleased"); use word boundaries. Two
   enabled packs that tie are chosen arbitrarily; make the choice deterministic.
3. Re-applying a pack writes another CREATED audit row saying `enabled: false`; re-enabling resets
   `enabled_at`. Only write audit rows for real changes.
4. N-077 Safe Harbor wording (`SAFE_HARBOR_NOTICE`): name what the profile removes and say the other
   identifiers need a manual pass; never "HIPAA compliant".

### 2. Build the small decided rules (N-077)
- Conversation retention counts from the last message, not the start.
- `/no-access` redirects to the workspace picker; refresh the signed-in context when a
  `TENANT_SUSPENDED` error arrives; pin it in `frontend/e2e/tests/49-account-and-links.spec.ts`.
- Batch page: a note next to "Tag documents" that switching it off keeps the tags already written.

### 3. Session 3 leftovers not reached
- Unit economics: a period with no usage reads "Suppressed — too little cost is known", identical to
  a real suppression; say "No usage in this period".
- RevOps console: show a promo code's redemptions (the API exists: F-372).
- The "Issue key" dialog needs `role="dialog"`, a name and the shared `useDialogFocus`.
- BYOK "Retire" needs the shared `ConfirmDialog`.

### 4. Close the browser-test gaps (highest value first)
A survey in session 4 found these features whose main actions no browser test performs. Write one
spec per feature (on a tenant of its own when it changes shared settings; see
`frontend/e2e/tests/75-…77-*.spec.ts` for the pattern: `newCustomer`, `standIn("subscribe", …)`):
1. **ERP posting** (`erp`): "Post N", posting "Retry", "Do not post", target "Test connection",
   "Set credential", mapping "Save as new version" / "Restore default".
2. **Payment risk and anomalies** (`radar`): "Confirm risk", "Not a risk…" + reason + "Dismiss",
   "This is a real finding", "Not an anomaly".
3. **Legal holds** (Settings → Workspace): "Place hold" with a reason, then deleting a held document
   is refused, "Release".
4. **Sessions and SSO enforcement**: "End session", "Sign out everywhere" (a fresh user, never a
   seeded one), "Require single sign-on" with its bypass, role mapping "Save mapping".
5. **Entity 360**: "Rename", "Erase record", "Split into a new record", "Undo merge".
6. **Seats and RevOps money actions**: "One seat fewer" → "Confirm", "Remove N unused seats";
   RevOps "Publish" confirmed, "Create code", "Void", "Issue due invoices".
7. **Review hub decisions per kind** (`verification`): ERP, table, obligation, merge and
   corroboration items decided in the browser; bulk "All pass" / "Dismiss all" against the real API.
8. **Tables**: "Accept figures", "Reject table", correcting a cell.
Then: three-way-match "Flag dispute"; supplier-cost "Record invoice" / "Reconcile"; partner manifest
→ marketplace install; document bulk "Reprocess" and "Retry processing"; workspace archive and logo
from the page; corroboration decisions; packet "Approve split"; case detail actions; obligation
"Waive" / feeds "Revoke"; identity OIDC and SCIM rotate/revoke; credential rotation on API keys,
webhooks and BYOK; data residency "Save region"; egress rule removal; process agent "Save policy"
and proposal "Reject" / "Undo"; leave organization / decline invitation; recovery codes and
"Stop requiring two-factor"; automation rule toggle/test/edit/duplicate/delete; clause assertion
"It passes" / "Wrong paragraph" / "Test on a document"; extraction memory "Reset" / "Retire";
AI / email / document settings "Test connection" / "Send test"; analytics "Sync now"; sovereign
licence "Verify and install"; the resumable upload tray (pause, resume, retry, cancel).

### 4b. Test-environment oddity seen in session 4
On a freshly rebuilt local e2e database, the first browser run's `standIn("subscribe", …)` failed with
`UnmappableTierError: No published quota tier '…' is in force` and the next run passed (the tiers'
`effective_from` was later than the first run's request). CI, which seeds a fresh database every run,
never showed it. Find out where the local first run publishes the tiers (global setup,
`backend/scripts/seed_e2e.py`, `start-stack.sh`) and make the order deterministic.

### 5. Later (not launch blockers)
Tailwind 4 with a page-by-page visual comparison; least-privilege database roles (N-074);
`docs/PRODUCT-NEXT.md` items when the owner chooses them.

## Finish line for the next session
Every item above is fixed with a failing-test-first proof, or recorded as unverified/blocked with
its reason; the backend suite and the browser suite pass; CI is green on the pull request; FINDINGS,
COVERAGE.csv (`campaign_slices.py --check all` exits 0), STATE, HANDOFF and NEEDS-OWNER are updated;
the pull request description explains the changes for a non-engineer.
