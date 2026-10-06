# FlowPilot AI — Runbook

How to run FlowPilot AI on a developer machine from a fresh clone, how to check
that it works, and what to do when it does not. Every command here was run
during hardening Phase 1 (2026-09-30) unless it is marked *(not run here)*.
`./start_dev.sh` was proven from a fresh clone on Linux; `start_dev.ps1` has
not been run on Windows yet.

Production deployment (a Linux VPS running Docker Compose; see
`docs/hardening/NEEDS-OWNER.md` N-007 and N-009) is section 9. Nothing in
section 9 has been run on a real server: the environment that wrote it cannot
pull Docker images. What was checked, and how, is stated at the top of section 9,
and every command that starts containers is marked *(not run here)*.

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

### 5.1 Browser tests (Playwright)

The end-to-end browser suite lives in `frontend/e2e` and has its own guide:
`frontend/e2e/README.md` (what to add to `backend/.env`, how to start the API, worker
and mail catcher, and how to read the report). Short version, with Postgres and Redis up:

```bash
frontend/e2e/scripts/start-stack.sh          # API + worker + mail catcher
cd frontend && npx playwright test -c e2e     # ~45 minutes, 2 browsers
npx playwright show-report e2e/playwright-report
```

What it covers and what it found: `docs/hardening/03-coverage.md`.

---

## 6. Things that are unusual about this project

- **`ARCH40_CONTRACT=1`.** The newest migration drops three old columns and
  refuses to run without this flag, so that a production deploy never drops
  columns by accident. Tests, CI and local development set it. Production runs
  it once, deliberately, after a backup (section 9.2, step 6); see NEEDS-OWNER N-009.
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
| Uploads fail with `InvalidAccessKeyId` | a MinIO data volume created before F-027 was fixed still has your shell's `AWS_ACCESS_KEY_ID` as its root user (the dev compose now uses `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`, default `minioadmin`) | `docker compose rm -sf minio minio-init && docker volume rm flowpilot_minio_data && docker compose up -d minio minio-init` |
| Web app shows "blocked by CORS policy" | `CORS_ORIGINS` does not list `http://localhost:5173` (the code default is only `:3000`) | keep the `CORS_ORIGINS` line from `.env.example` |
| Login fails right after `seed_admin.py` says `already-seeded` | the script prints `(unchanged)` in place of the password | use the password from the first seed (default `FlowPilot!Dev123`) |
| Upload ends `FAILED` with `ck_document_chunks_bbox_is_object` in the worker log | bug F-020, fixed in Phase 2 | you are running an old build: `git pull`, then restart the worker |
| Postgres password errors after changing `backend/.env` | the data volume keeps the first user and password | `docker compose down -v` (deletes local data), then start again |
| Launcher step 6 says `seed_… FAILED: …` | the seed script exited non-zero; the message is its last line | run the command from §3.5 by hand to see the full error |
| Everything is slow on the first run | the first `pip install` downloads about 6.5 GB | wait; later runs skip it |

---

## 9. Production on a VPS (Docker Compose)

Read this section once, top to bottom, before your first deploy. It covers one Linux
server running `backend/docker-compose.prod.yml` (owner decision N-007).

**What was checked, and how.**

- The compose file was rendered with `docker compose config`, and the application was
  started from exactly the environment the `web` service receives. It boots in
  production, and a bad value is refused with a list of every problem.
- The backup, restore-drill and monitor-ping scripts were run against a real PostgreSQL
  with the real `pg_dump`, `pg_restore` and `openssl`.
- The sweep scheduler wrapper was run with a stand-in for the container runner.
- **Not run:** every command that starts or talks to containers, because the environment
  that wrote this cannot pull Docker images. They are marked *(not run here)*. **Watch your
  first deploy** and use the checks in step 10.

Two shortcuts, used below. Run them once in each terminal session:

```bash
cd /srv/flowpilot/backend
export COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"
```

### 9.1 What you need

| Thing | What | Why |
|---|---|---|
| Server | Linux (Ubuntu 24.04 or Debian 12), 4 vCPU, 16 GB RAM, 100 GB disk | the compose file is sized for this; the OCR worker alone uses about 2.5 GB |
| Domain | a DNS "A" record (for example `app.example.com`) pointing at the server | Caddy gets a free HTTPS certificate by itself, but only when DNS is right and ports 80 and 443 are reachable |
| Docker | Engine 24+ with the Compose plugin | runs everything |
| Node.js | 22+, on any machine | builds the web app (`frontend/dist`), which Caddy serves |
| File storage | AWS S3, Cloudflare R2 or Backblaze B2 (recommended), or the optional MinIO service | one server disk is not durable storage for customers' documents |
| Outgoing email | an SMTP provider (Postmark, SES, ...) | the app **refuses to start** without one: invitations, verification and password-reset emails depend on it |
| Payments | Stripe **test** keys and/or Dodo **test** keys | stay in test mode until you decide to go live; never put live keys on a server you are still testing |
| An AI key | `GROQ_API_KEY` (or `GEMINI_API_KEY`) | without one, every AI feature errors for customers who have not brought their own key |

Only ports 80, 443/tcp and 443/udp are published (by Caddy). Postgres, Redis, MinIO and the
API are not reachable from outside. Do not add `ports:` to them.

### 9.2 First deploy

**1. Prepare the server** *(not run here)*. As root:

```bash
adduser --disabled-password --gecos "" flowpilot
usermod -aG docker flowpilot     # the docker group is as powerful as root here: protect this user's SSH keys
mkdir -p /srv/flowpilot /etc/flowpilot && chown flowpilot: /srv/flowpilot
```

Open only SSH, 80/tcp, 443/tcp and 443/udp in the firewall.

**2. Get the code** *(not run here)*. As the `flowpilot` user:

```bash
git clone <your repository URL> /srv/flowpilot
cd /srv/flowpilot/backend
```

**3. Fill in the configuration.**

```bash
cp .env.production.template .env.production
chmod 600 .env.production
```

Open `.env.production` and fill in every line that is empty or says `example.com`. Write
values without quotes. Generate the secrets with these commands, one value per setting, and
**never reuse one value for two settings**:

```bash
openssl rand -hex 32   # JWT_SECRET_KEY
openssl rand -hex 32   # API_KEY_PEPPER
openssl rand -hex 32   # REDIS_IDENTITY_PEPPER
openssl rand -hex 32   # RERANKER_INTERNAL_TOKEN
openssl rand -hex 24   # POSTGRES_PASSWORD (hex only: it is placed inside a URL)
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # EMAIL_ENCRYPTION_KEYS
```

Put a copy of `.env.production` in your password manager. Losing `EMAIL_ENCRYPTION_KEYS` makes
the credentials the app stores encrypted for customers (email, ERP and similar) unreadable.

The app **refuses to start** and prints every problem at once if: a secret is missing, short,
a known default or reused; `CORS_ORIGINS` is not an explicit `https://` address (no `*`, no
localhost); the database password is weak; debug logging is on; uploads would go to the
container's own disk; Redis, the mail server or the reranker token is missing; the Stripe or
Dodo keys disagree with the payment mode; or staging is in live payment mode. A refusal is
the app protecting you: fix the line it names.

**4. Build the web app and the images.**

```bash
( cd /srv/flowpilot/frontend && npm ci && npm run build && node scripts/check-no-sourcemaps.mjs --dist )
$COMPOSE build      # about 10 GB and 15 to 30 minutes the first time (F-045) - not run here
```

`frontend/dist` is what Caddy serves. The last check must exit 0: it fails if the build
would publish source maps (your original code).

**5. Check the configuration without starting anything.**

```bash
$COMPOSE config > /dev/null && echo "compose file OK"
```

An error names the missing variable, for example `required variable JWT_SECRET_KEY is
missing a value`. Never paste the output of `$COMPOSE config` anywhere: it prints your secrets.

**6. Create the database and run the migrations, once** *(not run here)*.

The newest migration (ARCH-40 "contract", N-009) permanently drops three unused columns
from `ai_settings`, so it refuses to run unless you say so. Their values are copied into
`settings_migration_archive` first, and the migration's `downgrade` puts them back. On a
brand-new database there is nothing to lose. On a database that has customers, take a backup
first (section 9.4).

```bash
$COMPOSE up -d db redis
ARCH40_CONTRACT=1 $COMPOSE run --rm migrate              # alembic upgrade head, with the flag, once
$COMPOSE run --rm --no-deps migrate alembic current      # must print one revision marked (head)
```

Leave `ARCH40_CONTRACT=0` in `.env.production`. That step is done, and every later deploy runs
the remaining migrations without the flag.

**7. Start everything** *(not run here)*.

```bash
$COMPOSE up -d
$COMPOSE ps                            # every service "running"; web says "healthy"
$COMPOSE logs --tail 50 web            # a refusal to start lists exactly what to fix
```

Caddy needs up to a minute to obtain its certificate. Then, from your own computer:

```bash
curl -fsS https://app.example.com/api/v1/health/ready      # {"status":"ready"}   (your domain)
```

**8. Create the plans and the first administrator, once** *(not run here)*.

Plans first. The plan names, limits and prices are defined in `backend/scripts/seed_quota_tiers.py`
(Developer $49, Business $299 and Enterprise $799 per seat per month at the time of writing);
**confirm them before launch (N-011)**. In your Stripe (or Dodo) dashboard, in **test mode**,
create the three products and prices, paste their ids into `GATEWAY_PRICE_ID_DEVELOPER`,
`GATEWAY_PRICE_ID_BUSINESS` and `GATEWAY_PRICE_ID_ENTERPRISE` in `.env.production`, then:

```bash
$COMPOSE up -d      # hands the ids to the containers
$COMPOSE exec -T web python scripts/seed_price_book.py --version 1
$COMPOSE exec -T web python scripts/seed_quota_tiers.py
```

The second command refuses a paid plan that has no price id. Do not add `--allow-unpriced` in
production: it publishes a plan nobody can buy, and a published plan version cannot be
changed.

Then the first administrator. Put a long password in the two `SEED_ADMIN_*` lines of
`.env.production` and run:

```bash
E="$(grep '^SEED_ADMIN_EMAIL=' .env.production | cut -d= -f2-)"
P="$(grep '^SEED_ADMIN_PASSWORD=' .env.production | cut -d= -f2-)"
$COMPOSE exec -T -e SEED_ADMIN_EMAIL="$E" -e SEED_ADMIN_PASSWORD="$P" web python scripts/seed_admin.py --json
unset E P
```

Now **delete the two `SEED_ADMIN_*` lines** from `.env.production`, sign in, and change the
password. (The compose file deliberately does not pass these two to the running containers.)

**9. Point billing at your server (test mode)** *(not run here)*.

In Stripe (Developers, Webhooks) add the endpoint `https://app.example.com/api/v1/billing/webhooks/stripe`
and copy its signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRETS` (several secrets may be
listed, separated by commas, while you rotate one). For Dodo the endpoint is
`https://app.example.com/api/v1/billing/webhooks/dodo` and the secret goes in
`DODO_WEBHOOK_SECRET`. Use the lowercase gateway name. After editing `.env.production`, run
`$COMPOSE up -d`. The app refuses unsigned events, and refuses to start when a payment API key
has no webhook secret.

**10. Check it from outside** *(not run here)*.

- `https://app.example.com` loads and you can sign in.
- `curl -sI https://app.example.com | grep -i strict-transport` shows the HTTPS-only header.
- `curl -s -o /dev/null -w "%{http_code}\n" https://app.example.com/docs` prints `404`: the
  API's documentation page and route list are not public in production (F-046).
- `curl -s -o /dev/null -w "%{http_code}\n" https://app.example.com/api/v1/internal/tls/authorize`
  prints `404`: the internal namespace is not published.
- Upload a small PDF. The first document is slow because the OCR and search models download on
  first use, so the server needs outbound internet until they are cached.
- Set up backups and scheduled jobs the same day (sections 9.4 and 9.6).

### 9.3 Deploying a new version

```bash
cd /srv/flowpilot && git pull
( cd frontend && npm ci && npm run build && node scripts/check-no-sourcemaps.mjs --dist )
cd backend
set -a; . /etc/flowpilot/sweepers.env; set +a
deploy/bin/flowpilot-compose-backup       # a fresh backup first (section 9.4)
$COMPOSE build
$COMPOSE up -d                            # the migrate service runs first; web waits for it
$COMPOSE ps && $COMPOSE logs --tail 30 web
```

If `migrate` fails, the new API does not start. Read `$COMPOSE logs migrate`. If the database
was left half-changed, restore the backup you just took (section 9.5).

**When a release changes the plan table** (`backend/scripts/seed_quota_tiers.py`), publish the new
tier versions and move live subscribers onto them. Phase 5 is such a release: Business and
Enterprise now include `capability.byok` (owner decision N-021). A database seeded before it keeps the
old versions, and its Business/Enterprise tenants are refused BYOK writes until you run:

```bash
$COMPOSE run --rm web python scripts/seed_quota_tiers.py --carry-forward
```

The seed publishes only the tiers that changed; `--carry-forward` moves a live subscription only
when the new version costs the same and takes nothing away.

### 9.4 Backups

| What | How | Where it ends up |
|---|---|---|
| The database (every customer's data, users, plans) | nightly encrypted `pg_dump`, read back and verified, by `deploy/bin/flowpilot-compose-backup`; the newest 7 daily and 4 weekly copies are kept | `/srv/flowpilot/backups`, plus an off-server copy if you set one up (below) |
| Uploaded documents | they live in the file store, **not** in the database dump | the bucket (see "File storage" below) |
| Secrets | `.env.production` and `/etc/flowpilot/backup.key` | your password manager |

Set up once *(not run here)*:

```bash
sudo install -d -o flowpilot -g flowpilot /srv/flowpilot/backups /srv/flowpilot/logs /srv/flowpilot/locks
sudo install -m 600 -o flowpilot -g flowpilot /dev/null /etc/flowpilot/backup.key
openssl rand -base64 48 | sudo tee /etc/flowpilot/backup.key > /dev/null
sudo cat /etc/flowpilot/backup.key    # copy it into your password manager NOW
```

**Without that key every backup is unreadable. Keep a copy off the server.** Then create
`/etc/flowpilot/sweepers.env` (section 9.6, which also installs the schedule). Run a backup and a
restore drill by hand once, and read what they print:

```bash
set -a; . /etc/flowpilot/sweepers.env; set +a
deploy/bin/flowpilot-compose-backup            # ends with "backup ok: ..."
deploy/bin/flowpilot-compose-restore-drill     # restores the newest backup into a scratch database
```

The drill compares row counts and the migration revision with the live database, drops the
scratch database, and prints the restore time. **That time is how long a recovery takes for a
database of this size.** It never touches the live database.

**An off-server copy.** A backup that sits on the server dies with the server. Set
`FLOWPILOT_BACKUP_MIRROR_CMD` in `sweepers.env` to any command that copies the file whose path
arrives as `$1`, for example `aws s3 cp "$1" s3://YOUR-BACKUP-BUCKET/flowpilot/` or
`rclone copyto "$1" remote:flowpilot/$(basename "$1")`. Use a different account or provider than
your documents bucket, with a key that can add files but not delete them. The files are already
encrypted. If the copy fails, the run reports failure (and pings the failure address) while the
local backup stays.

**File storage.**

- S3, R2 or B2 (recommended): turn on bucket **versioning**, add a rule that expires old
  versions after about 30 days, and give the app a key that can read, write and delete objects
  in that one bucket and nothing else. Add provider replication, or a scheduled `rclone sync` to
  a bucket at another provider, so one account problem cannot take every copy.
- The optional MinIO service keeps files in a Docker volume on the same disk, so it is **not a
  backup of anything**. If you use it, copy the bucket to another provider on a schedule with `mc
  mirror` or `rclone`. This is not automated here, and is the main reason to prefer S3, R2 or B2.

### 9.5 Restoring after a disaster

*(not run here: rehearse it once on a spare server before you need it.)* If the whole server is
gone: build a new one (steps 1 to 5 of section 9.2), put your saved `.env.production` and
`/etc/flowpilot/backup.key` back, run `$COMPOSE up -d db redis`, and restore as below. If your
`POSTGRES_USER` or `POSTGRES_DB` is not `flowpilot`, use yours.

```bash
BACKUP=/srv/flowpilot/backups/flowpilot-YYYYMMDDTHHMMSSZ.dump.enc     # the file you chose
( cd "$(dirname "$BACKUP")" && sha256sum -c "$(basename "$BACKUP").sha256" )    # must say OK

# 1. stop everything that writes
$COMPOSE stop web worker-scheduler worker-light worker-relay worker-delivery worker-stripe worker-ocr worker-enrich

# 2. restore into a NEW database, leaving the current one untouched
$COMPOSE exec -T db psql -U flowpilot -d postgres -c 'CREATE DATABASE flowpilot_restored'
$COMPOSE exec -T db psql -U flowpilot -d flowpilot_restored -c 'CREATE EXTENSION IF NOT EXISTS vector'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:/etc/flowpilot/backup.key -in "$BACKUP" \
  | $COMPOSE exec -T db pg_restore --no-owner --exit-on-error -U flowpilot -d flowpilot_restored

# 3. look before you swap
$COMPOSE exec -T db psql -U flowpilot -d flowpilot_restored -c 'SELECT count(*) FROM users' -c 'SELECT version_num FROM alembic_version'

# 4. swap it in; the old database is kept, renamed
$COMPOSE exec -T db psql -U flowpilot -d postgres \
  -c 'ALTER DATABASE flowpilot RENAME TO flowpilot_replaced' -c 'ALTER DATABASE flowpilot_restored RENAME TO flowpilot'
$COMPOSE up -d       # migrations bring an older backup up to the current version
```

If the rename says the database is being accessed by other users, something is still connected:
run `$COMPOSE stop` on the remaining services and try again. Delete `flowpilot_replaced` only when
you are sure. The database dump does not contain uploaded documents; they come back from the
bucket (versioning, above).

### 9.6 Scheduled jobs

Two kinds of recurring work:

1. **Inside the stack.** `worker-scheduler` starts usage rollups, billing dunning, certificate
   renewal, warehouse pushes and partner revenue share; the other workers run the queued jobs.
   `$COMPOSE up -d` starts them. **Never remove `worker-scheduler`**: nothing is billed without it.
2. **On the server, by cron.** The nightly sweeps (data retention and erasure, obligation
   reminders, ERP retries, invitation and identity clean-up, and more) and the backups. The app does
   not start these itself, and until Phase 2 a Compose deployment had nothing to start them: your
   retention promises would have silently not been kept (F-006).

Install once *(not run here)*:

```bash
sudo tee /etc/flowpilot/sweepers.env > /dev/null <<'EOF'
# Read by cron as a shell file: keep the quotes.
FLOWPILOT_RUNNER="docker compose -f /srv/flowpilot/backend/docker-compose.prod.yml --env-file /srv/flowpilot/backend/.env.production run --rm --no-deps -T -e SERVICE_ROLE=sweeper worker-light python"
FLOWPILOT_LOG_DIR=/srv/flowpilot/logs
LOCK_DIR=/srv/flowpilot/locks
FLOWPILOT_BACKUP_DIR=/srv/flowpilot/backups
FLOWPILOT_BACKUP_KEY_FILE=/etc/flowpilot/backup.key
# Copy every backup off this server (the file path arrives as $1):
# FLOWPILOT_BACKUP_MIRROR_CMD='aws s3 cp "$1" s3://YOUR-BACKUP-BUCKET/flowpilot/'
# A free Healthchecks.io account emails you when a job fails or does not run. One check per job:
# HEARTBEAT_BASE=https://hc-ping.com
# HEARTBEAT_UUID_COMPOSE_BACKUP=<check id>
# HEARTBEAT_UUID_COMPOSE_RESTORE_DRILL=<check id>
# HEARTBEAT_UUID_COMPLIANCE=<check id>
EOF
sudo chown root:flowpilot /etc/flowpilot/sweepers.env && sudo chmod 640 /etc/flowpilot/sweepers.env
sudo install -m 644 /srv/flowpilot/backend/deploy/cron.d/flowpilot-sweepers /etc/cron.d/flowpilot-sweepers
sudo install -m 644 /srv/flowpilot/backend/deploy/cron.d/flowpilot-compose-backups /etc/cron.d/flowpilot-compose-backups
# The three point-in-time-recovery lines need PostgreSQL WAL archiving, which this setup does not
# have (N-012). Switch them off:
sudo sed -i -E 's/^([^#].*flowpilot-sweep (dr-heartbeat|base-backup|pitr-drill).*)$/# \1/' /etc/cron.d/flowpilot-sweepers
```

Do **not** install `flowpilot-backups`: that file is for a server with Python and the Postgres tools
installed directly, not for Compose. Check it works now, not at 3 a.m.:

```bash
/srv/flowpilot/backend/deploy/bin/flowpilot-sweep compliance --report    # prints each tenant's retention policy, changes nothing
tail -n 20 /srv/flowpilot/logs/sweep_compliance.log                      # a "SWEEP_..." line and no error
```

After the first night, look at the logs in `/srv/flowpilot/logs` (`sweep_*.log`,
`compose_backup.log`, `compose_restore_drill.log`), or better, let the monitor tell you.

### 9.7 What to look at when something is wrong

| Symptom | Look here |
|---|---|
| The site does not load | `$COMPOSE ps`, then `$COMPOSE logs --tail 100 caddy web` |
| `web` restarts in a loop | `$COMPOSE logs web`: a configuration refusal lists every setting to fix |
| Emails never arrive | `PLATFORM_SMTP_*` in `.env.production`; then `$COMPOSE logs worker-delivery` |
| AI features answer with an error | `GROQ_API_KEY` (or the customer's own key) is missing or wrong |
| Payments are not confirmed | webhook secret in `.env.production` (section 9.2 step 9); `$COMPOSE logs worker-stripe web` |
| The disk is filling up | `docker system df`; old images: `docker image prune`; old backups are pruned by the script |
| A cron job seems not to run | `/srv/flowpilot/logs`, `grep CRON /var/log/syslog`, and your Healthchecks page |
| People in one office are told "Rate limit exceeded" when signing in | `RATE_LIMIT_LOGIN_IP_PER_5MIN` in `.env.production` (default 20 per address per 5 minutes; decided N-018), then `$COMPOSE up -d web` |
| A Business/Enterprise tenant is refused BYOK ("included on higher plans") | the plan seed was not re-run after the Phase 5 release (section 9.3) |
