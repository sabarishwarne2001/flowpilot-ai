# FlowPilot AI — Working Rules for Claude Code

## Project Overview
Multi-tenant B2B AI document intelligence & workflow automation SaaS.
Backend: FastAPI, SQLAlchemy 2, PostgreSQL 16 + pgvector, Alembic, Redis, MinIO,
PaddleOCR, SentenceTransformers, Groq. Frontend: React 19, TypeScript, Vite,
TanStack Query, Tailwind. Billing: Stripe and Dodo Payments.
The owner is a solo founder who is new to large-scale SaaS development.
Explain findings in plain language; never assume prior DevOps knowledge.

## Campaign Tracking Files
- docs/hardening/STATE.md: Current phase, next action, blockers, budget notes.
- docs/hardening/COVERAGE.csv: Ledger tracking status (untested | smoke | deep | blocked).
- docs/hardening/FINDINGS.md: Discovered bugs, severity, and evidence.
- docs/hardening/NEEDS-OWNER.md: Product decisions only the owner can make.

Read STATE.md at the start of every session. Update it before stopping.

## Hard Rules
- Work ONLY on branches named `hardening/<topic>`. NEVER push directly to `main`.
- One logical fix per commit. Commit messages must state what and why.
- Never weaken, skip, or delete an existing test to make CI pass.
- Never commit secrets, real API keys, `.env` files, or binaries (.exe, .bin).
- Use only test-mode credentials. Never contact live payment or production endpoints.
- Alembic migrations must maintain exactly ONE head.
- Do NOT read or edit historical scripts: `apply_*.py`, `verify_*.py`, `backend/evidence/`,
  `backend/arch07_*`, `backend/arch08_*`, `*.pdf`, `*-FINAL-CERTIFICATION.md`.
- Keep terminal and command outputs short (use `| tail -50`, `--maxfail=5`, `-q`).
- Do not decide product/pricing questions. Log them in `NEEDS-OWNER.md`.

## Evidence Rule
A bug is considered "fixed" ONLY when:
1. A failing test proved it existed.
2. The code fix was applied.
3. That specific test now passes.
4. The rest of the suite continues to pass.
Report anything not proven as "unverified".

## Budget & Loop Guard
- Stop at each designated phase checkpoint and report.
- If you make 3 unsuccessful attempts to fix a single bug, STOP, record the issue in
  `docs/hardening/BLOCKERS.md`, and move on to the next item.