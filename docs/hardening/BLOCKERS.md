# Blockers

Issues that could not be fixed after 3 attempts (loop guard in CLAUDE.md).
Each entry lists what was tried and what is needed to unblock.

_None so far._ Phase 0 made no fix attempts; in Phase 1 no fix needed more than two
attempts (the OCR stub took two); in Phase 2 no fix needed three.

Not blockers, but deliberately not done, each with its reason in `FINDINGS.md`:

- **Starlette / FastAPI upgrade** (F-039): a framework upgrade, not a patch. Needs the owner's
  go-ahead (N-014).
- **python-jose 3.4.0** (F-039): its fix pins `pyasn1<0.5`, which conflicts with the patched
  `pyasn1`. The clean fix is a swap to PyJWT.
- **Docker-based checks** (F-006, F-040): the sandbox cannot pull images (Docker Hub rate limit),
  so nothing was run in containers. Everything that needs it is marked "not run here".
- **Dodo mode guard** (F-033): cannot be fixed without a real payload (N-013).
