# FlowPilot AI — Runbook

How to run FlowPilot AI on a developer machine from a fresh clone, how to check
that it works, and what to do when it does not. Every command here was run
during hardening Phase 1 (2026-09-30) unless it is marked *(not run here)*.

Production deployment is not covered yet. Phase 2 writes that section (the
target is a Linux host running Docker Compose; see
`docs/hardening/NEEDS-OWNER.md` N-007 and N-009).

---

## 1. What you need

| Tool | Version | Why |
|---|---|---|
| Git | any | clone the repository |
| Docker | Engine 24+ or Docker Desktop | Postgres 16 + pgvector, Redis 7, MinIO |
| Python | **3.12** (3.13 also accepted by the launcher) | backend. 3.11 does **not** work: `scipy==1.18.0` has no 3.11 build |
| Node.js | **22+** | frontend (`package.json` → `engines.node >= 22`) |
| Disk | about 15 GB free | the Python packages alone are about 6.5 GB (torch with CUDA, paddle) |

Ports used: 5432 (Postgres), 6379 (Redis), 9000/9001 (MinIO), 8000 (API),
5173 (web app).

---

## 2. Quick start: one command

```bash
git clone https://github.com/sabarishwarne2001/flowpilot-ai.git
cd flowpilot-ai
./start_dev.sh            # Linux / macOS
```

```powershell
git clone https://github.com/sabarishwarne2001/flowpilot-ai.git
cd flowpilot-ai
.\start_dev.ps1           # Windows PowerShell   (not run here: no Windows in the sandbox)
```

The launcher:

1. checks Docker, Python 3.12+ and Node;
2. creates `backend/.venv` and installs `requirements.txt` +
   `requirements-dev.txt` (the first run downloads several GB, and it only
   reinstalls when those files change);
3. creates `backend/.env` from `.env.example` and generates the secrets
   (`JWT_SECRET_KEY`, `API_KEY_PEPPER`, `REDIS_IDENTITY_PEPPER`,
   `RERANKER_INTERNAL_TOKEN`, `EMAIL_ENCRYPTION_KEYS`);
4. starts `db`, `redis`, `minio` and `minio-init` with Docker Compose;
5. runs `alembic upgrade head` (with `ARCH40_CONTRACT=1`, see §6);
6. seeds the price book, the plan tiers (`--allow-unpriced`, because dev has no
   payment gateway) and an admin account;
7. starts the API (port 8000), one worker (`--loop all`), and the web app (port
   5173).

Then open <http://localhost:5173> and sign in with:

- email `admin@flowpilot.ai`
- password `FlowPilot!Dev123` (the development default in
  `backend/scripts/seed_admin.py`; the launcher prints the real values)

Stop everything with `./start_dev.sh --stop`. Start from an empty database with
`./start_dev.sh --reset`, which **deletes all local data**.

---

## 3. The same thing by hand (Linux / macOS)

Use this when the launcher fails and you need to see which step broke.

```bash
cd flowpilot-ai/backend

# 3.1 Configuration
cp .env.example .env
# Fill in the five secrets. Each command prints a value to paste into .env:
openssl rand -hex 32     # JWT_SECRET_KEY
openssl rand -hex 32     # API_KEY_PEPPER
openssl rand -hex 32     # REDIS_IDENTITY_PEPPER
openssl rand -hex 32     # RERANKER_INTERNAL_TOKEN  (compose refuses to start without it)
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # EMAIL_ENCRYPTION_KEYS
# The MinIO settings (STORAGE_BACKEND=minio, S3_BUCKET, S3_ENDPOINT_URL,
# AWS_ACCESS_KEY_ID=minioadmin, AWS_SECRET_ACCESS_KEY=minioadmin) and
# CORS_ORIGINS are already in .env.example; leave them as they are.

# 3.2 Services. Compose reads backend/.env, so Postgres gets the same user
# and password as the app.
docker compose up -d db redis minio minio-init
docker compose ps          # db, redis and minio should say "healthy"

# 3.3 Python packages
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 3.4 Database schema
alembic heads                           # must print exactly one "(head)"
ARCH40_CONTRACT=1 alembic upgrade head

# 3.5 Seed data
python scripts/seed_price_book.py --version 1
python scripts/seed_quota_tiers.py --allow-unpriced
python scripts/seed_admin.py --json

# 3.6 Run (three terminals, each with the venv active and in backend/)
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m app.worker --loop all --profile all --log-level INFO
cd ../frontend && npm ci && npm run dev
```

On Windows the steps are the same. Use `.venv\Scripts\activate` and set the
flag with `$env:ARCH40_CONTRACT = '1'` before `alembic upgrade head`.

---

## 4. Is it working? Checks

| Check | Command | Expected |
|---|---|---|
| API alive | `curl http://127.0.0.1:8000/api/v1/health` | `200` and `"status":"healthy"` |
| API schema | `curl -o openapi.json http://127.0.0.1:8000/api/v1/openapi.json` | `200`; 441 paths / 550 operations at Phase 1 |
| One migration head | `cd backend && alembic heads` | exactly one line ending `(head)` |
| Migration round trip | `ARCH40_CONTRACT=1 alembic downgrade -1 && ARCH40_CONTRACT=1 alembic upgrade head` | both exit 0 |
| No new schema drift | `ARCH40_CONTRACT=1 python scripts/check_migration_drift.py` | `No new drift. 314 known operation(s) remain` (see F-017) |
| Encodings | `cd backend && python scripts/normalize_encodings.py --check` | `Encoding clean` |
| Verification gates | `cd backend && python scripts/run_all_gates.py --static-only` | runs all 78; **33 fail today** (F-029) |
| Backend tests | `cd backend && pytest -q` | **red today** (F-016); see §5 |
| Frontend types | `cd frontend && npx tsc --noEmit` | no output, exit 0 |
| Frontend lint | `cd frontend && npm run lint` | exit 0 |
| Frontend build | `cd frontend && npm run build` | `✓ built`, exit 0 |

---

## 5. Running the backend tests

The tests need the Postgres and Redis from §3.2. They create their own
database (`flowpilot_test`), migrate it to head and drop it at the end, so
your development data is never touched. `pytest.ini` sets `ENVIRONMENT=test`
and `ARCH40_CONTRACT=1` for you.

```bash
cd backend && . .venv/bin/activate
pytest -q --maxfail=5          # what CI runs
pytest -q -rfE                 # full run with a failure list (slow, see below)
pytest tests/services/test_ml_stubs.py -q    # one file (this one needs no database)
```

- **Speed.** Each DB test truncates about 200 tables twice, so a full run takes
  roughly 2 hours on a normal disk. For a fast local run, start a throwaway
  Postgres on a RAM disk and point the tests at it (about 25 minutes):

  ```bash
  docker run -d --name flowpilot-testdb -p 5433:5432 \
    -e POSTGRES_USER=flowpilot -e POSTGRES_PASSWORD=flowpilot -e POSTGRES_DB=flowpilot \
    --tmpfs /var/lib/postgresql/data:rw,size=6g pgvector/pgvector:pg16 \
    -c fsync=off -c synchronous_commit=off -c full_page_writes=off
  POSTGRES_PORT=5433 POSTGRES_USER=flowpilot POSTGRES_PASSWORD=flowpilot pytest -q
  ```

- **Do not run two pytest processes against the same Postgres.** Some tests
  use a fixed database name, and parallel runs corrupt each other (F-030).

---

## 6. Things that are unusual about this project

- **`ARCH40_CONTRACT=1`.** The newest migration drops three old columns and
  refuses to run without this flag, so that a production deploy never drops
  columns by accident. Tests, CI and local development set it. Production does
  not yet; see NEEDS-OWNER N-009.
- **`DATABASE_URL` is ignored by the app.** The app builds its database
  address from `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD` and `POSTGRES_DB`. Only the backup scripts read
  `DATABASE_URL`.
- **MinIO images.** `minio/minio` and `minio/mc` no longer exist on Docker Hub.
  The compose files use pinned `pgsty/minio` and `pgsty/mc` builds (N-008).
- **ML model weights.** OCR (PaddleOCR) and embeddings (SentenceTransformers
  `all-MiniLM-L6-v2`) download their weights from the internet on first use.
  Where that is blocked, set `ML_STUBS=true` in `backend/.env` (see §7).

---

## 7. `ML_STUBS=true`: running without model weights

`ML_STUBS=true` replaces the two models with **clearly labelled test stubs**
(`backend/app/services/ml_stubs.py`):

- **Embeddings.** Deterministic 384-number vectors built from the words in the
  text. Texts that share words come out similar. This is *not* semantic search,
  and answer quality from the AI assistant means nothing with it.
- **OCR.** Digital PDFs still use their real text layer. Images and scanned PDF
  pages become the text `[FLOWPILOT TEST STUB OCR - NOT REAL TEXT] sha256=…`.
  No pixels are read.
- Chunks stored while stubs are on are labelled
  `embedding_model = 'flowpilot-test-stub-embedding'`. Before turning stubs off
  on a database that holds such chunks, re-process or delete those documents.
  To find them:
  `SELECT count(*) FROM document_chunks WHERE embedding_model = 'flowpilot-test-stub-embedding';`
- The app **refuses to start** with `ML_STUBS=true` and
  `ENVIRONMENT=production`.

Verified in Phase 1: with stubs on, a PNG, a scanned PDF and two digital PDFs
uploaded through the API all reach `COMPLETED`.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pull access denied for minio/minio` | the image was removed from Docker Hub | pull the latest `main`; the compose files use `pgsty/*` |
| `required variable RERANKER_INTERNAL_TOKEN is missing a value` | `backend/.env` has no token | `openssl rand -hex 32` → `RERANKER_INTERNAL_TOKEN=` in `backend/.env` |
| `alembic upgrade head` → `RuntimeError: arch40_step3_contract_ai_settings drops columns…` | the contract flag is not set | `ARCH40_CONTRACT=1 alembic upgrade head` (dev and test databases only) |
| `No matching distribution found for scipy==1.18.0` | Python 3.11 or older | use Python 3.12 |
| Uploads fail with `InvalidAccessKeyId` | your shell exports `AWS_ACCESS_KEY_ID`; Docker Compose used it as the MinIO root user (F-027) | `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY` (PowerShell: `Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY`), then `docker compose rm -sf minio minio-init && docker volume rm flowpilot_minio_data && docker compose up -d minio minio-init` |
| Web app shows "blocked by CORS policy" | `CORS_ORIGINS` does not list `http://localhost:5173` (the code default is only `:3000`) | keep the `CORS_ORIGINS` line from `.env.example` |
| Login fails right after `seed_admin.py` says `already-seeded` | the script prints `(unchanged)` in place of the password | use the password from the first seed (default `FlowPilot!Dev123`) |
| Upload ends `FAILED` with `ck_document_chunks_bbox_is_object` in the worker log | known bug F-020 | none yet; Phase 4 |
| Postgres password errors after changing `backend/.env` | the data volume keeps the first user and password | `docker compose down -v` (deletes local data), then start again |
| Everything is slow on the first run | the first `pip install` downloads about 6.5 GB | wait; later runs skip it |
