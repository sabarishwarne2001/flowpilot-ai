# Blockers

Issues that could not be fixed after 3 attempts (loop guard in CLAUDE.md).
Each entry lists what was tried and what is needed to unblock.

_None so far._ Phase 0 made no fix attempts; in Phase 1 no fix needed more than two
attempts (the OCR stub took two); in Phases 2 to 5 and the final release no fix needed three.

Not blockers, but deliberately not done, each with its reason in `FINDINGS.md`:

- **Starlette / FastAPI upgrade** (F-039): done in Phase 5 (N-014 approved): FastAPI 0.136.3,
  Starlette 1.7.0. FastAPI stays below 0.137 on purpose (STATE.md, environment notes).
- **python-jose** (F-039): replaced by PyJWT in Phase 5.
- **Docker-based checks** (F-006, F-040): the sandbox cannot pull images (Docker Hub rate limit),
  so nothing was run in containers. Everything that needs it is marked "not run here".
- **Dodo mode guard** (F-033): closed in Phase 5; Dodo's webhook envelope has no mode field.
- **CPU-only torch** (F-045 rest): download.pytorch.org is blocked here, so the swap that would drop
  the 3.7 GB CUDA stack from every image could not be verified.
- **AI results with stub models** (F-063): closed in the final release. `E2E_LLM=1` now starts a
  deterministic local model stand-in (`frontend/e2e/support/llm-mock.mjs`), so the model-dependent
  flows run in every browser run without a key or downloads.
