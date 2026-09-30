# FlowPilot AI

Multi-tenant B2B document intelligence and workflow automation. Customers
upload documents (PDFs, scans, images). FlowPilot extracts the text and
fields, lets people review and correct them, answers questions about them
with an AI assistant, and runs automations on the results.

| Part | Technology |
|---|---|
| API | FastAPI, SQLAlchemy 2, Alembic (`backend/app`) |
| Workers | `python -m app.worker` (job queue in Postgres) |
| Data | PostgreSQL 16 + pgvector, Redis 7, MinIO / S3 |
| AI | PaddleOCR, SentenceTransformers, Groq / Gemini |
| Web app | React 19, TypeScript, Vite, TanStack Query, Tailwind (`frontend/`) |
| Billing | Stripe and Dodo Payments |

## Quick start (development)

You need Docker, Python 3.12, Node.js 22 and about 15 GB of disk.

```bash
git clone https://github.com/sabarishwarne2001/flowpilot-ai.git
cd flowpilot-ai
./start_dev.sh          # Windows: .\start_dev.ps1
```

Open <http://localhost:5173> and sign in as `admin@flowpilot.ai` with the
password the launcher prints (development default `FlowPilot!Dev123`).
API health: <http://localhost:8000/api/v1/health>. API schema:
<http://localhost:8000/api/v1/openapi.json>.

No internet access to the model hosts? Put `ML_STUBS=true` in `backend/.env`
to use labelled test stubs in place of OCR and embeddings (never in
production).

**Full instructions, manual steps, test commands and troubleshooting:
[docs/RUNBOOK.md](docs/RUNBOOK.md).**

## Project status

A hardening campaign is in progress. Its plan, findings and progress are in
[`docs/hardening/`](docs/hardening/):

- [`STATE.md`](docs/hardening/STATE.md): current phase and next step
- [`FINDINGS.md`](docs/hardening/FINDINGS.md): known bugs, by severity
- [`01-baseline.md`](docs/hardening/01-baseline.md): what builds, runs and passes today
- [`NEEDS-OWNER.md`](docs/hardening/NEEDS-OWNER.md): open product decisions

As of Phase 1 the app installs, migrates, starts and serves the web app from a
fresh clone. The backend test suite and the verification gates are **not**
green yet (FINDINGS F-016, F-029).

## Repository layout

```
backend/            FastAPI app, workers, Alembic migrations, tests, scripts
  app/              application code
  alembic/          database migrations (exactly one head)
  tests/            pytest suite
  scripts/          seeding, gates (verify_*.py), maintenance
  deploy/           host cron files and deployment helpers
frontend/           React web app
docs/               runbook and hardening campaign documents
start_dev.sh/.ps1   one-command local development launchers
```

Files named `apply_*.py`, `verify_arch*.py`, `run_arch*.ps1` and
`*-FINAL-CERTIFICATION.md` are historical records of earlier development
phases. They are kept for reference and are not part of running the product.

## License

Proprietary. All rights reserved. This is closed-source commercial software;
no licence to use, copy, modify or distribute it is granted.
