#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
RUN_DIR="${REPO_ROOT}/.dev"
LOG_DIR="${RUN_DIR}/logs"
VENV_DIR="${BACKEND_DIR}/.venv"
PID_FILE="${RUN_DIR}/pids"

API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

DO_RESET=0
DO_STOP=0
NO_FRONTEND=0
SKIP_MIGRATIONS=0

C_RESET=$'\033[0m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[90m'

step() { printf '\n%s==> %s%s\n' "${C_CYAN}" "$1" "${C_RESET}"; }
ok()   { printf '    %s[ok]%s %s\n' "${C_GREEN}" "${C_RESET}" "$1"; }
warn() { printf '    %s[!]%s  %s\n' "${C_YELLOW}" "${C_RESET}" "$1"; }
die()  {
    printf '    %s[x]%s  %s\n' "${C_RED}" "${C_RESET}" "$1" >&2
    [[ $# -gt 1 ]] && printf '         %s%s%s\n' "${C_YELLOW}" "$2" "${C_RESET}" >&2
    exit 1
}

trap 'die "start_dev.sh failed at line ${LINENO}"' ERR

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reset)           DO_RESET=1 ;;
        --stop)            DO_STOP=1 ;;
        --no-frontend)     NO_FRONTEND=1 ;;
        --skip-migrations) SKIP_MIGRATIONS=1 ;;
        --api-port)        API_PORT="$2"; shift ;;
        --frontend-port)   FRONTEND_PORT="$2"; shift ;;
        -h|--help)         sed -n '2,12p' "$0"; exit 0 ;;
        *)                 die "unknown option: $1" ;;
    esac
    shift
done

hex_secret() {
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
    else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

fernet_key() {
    head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'
}

env_get() {
    local file="$1" key="$2"
    [[ -f "${file}" ]] || return 0
    sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "${file}" | head -n1 | tr -d '"'
}

env_set() {
    local file="$1" key="$2" value="$3"
    touch "${file}"
    if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "${file}"; then
        python3 - "$file" "$key" "$value" <<'PY'
import re, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
with open(path, "w", encoding="utf-8") as fh:
    for line in lines:
        fh.write(f"{key}={value}\n" if pattern.match(line) else line)
PY
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

wait_for_port() {
    local label="$1" port="$2" timeout="${3:-90}" waited=0
    while (( waited < timeout )); do
        if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
            exec 3>&- 2>/dev/null || true
            ok "${label} is accepting connections on ${port}"
            return 0
        fi
        sleep 1; waited=$((waited + 1))
    done
    return 1
}

stop_tracked() {
    [[ -f "${PID_FILE}" ]] || { warn 'No tracked processes.'; return 0; }
    while IFS='=' read -r name pid; do
        [[ -n "${pid:-}" ]] || continue
        if kill -0 "${pid}" 2>/dev/null; then
            echo "    stopping ${name} (pid ${pid})"
            kill -TERM "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
        fi
    done < "${PID_FILE}"
    sleep 2
    while IFS='=' read -r name pid; do
        [[ -n "${pid:-}" ]] || continue
        kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
    ok 'Tracked processes stopped.'
}

printf '\n  FlowPilot AI — local development launcher\n'
printf '  %s----------------------------------------%s\n' "${C_DIM}" "${C_RESET}"

if (( DO_STOP )); then
    step 'Stopping tracked processes'; stop_tracked
    step 'Stopping Docker backing services'
    (cd "${BACKEND_DIR}" && docker compose stop db redis minio >/dev/null 2>&1) || true
    ok 'Containers stopped.'
    exit 0
fi

mkdir -p "${RUN_DIR}" "${LOG_DIR}"

step '1/8  Verifying prerequisites'
command -v docker >/dev/null 2>&1 || die 'Docker is not on PATH.'
docker info >/dev/null 2>&1 || die 'The Docker daemon is not responding.'
ok 'Docker daemon is up.'

PYTHON_BIN=''
for candidate in python3.12 python3.13 python3; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    if "${candidate}" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
        PYTHON_BIN="${candidate}"; ok "Python: $("${candidate}" --version)"; break
    fi
done
[[ -n "${PYTHON_BIN}" ]] || die 'Python 3.12+ not found.'

if command -v node >/dev/null 2>&1; then
    ok "Node: $(node --version)"
elif (( NO_FRONTEND )); then
    warn 'Node.js absent; continuing because --no-frontend was passed.'
else
    die 'Node.js is not on PATH.'
fi

step '2/8  Python virtual environment'
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo '    creating backend/.venv ...'
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
VENV_PY="${VENV_DIR}/bin/python"
ok 'Virtual environment present.'

step '3/8  Environment configuration'
BACKEND_ENV="${BACKEND_DIR}/.env"
[[ -f "${BACKEND_ENV}" ]] || cp "${BACKEND_DIR}/.env.example" "${BACKEND_ENV}"

for key in JWT_SECRET_KEY API_KEY_PEPPER REDIS_IDENTITY_PEPPER; do
    if [[ -z "$(env_get "${BACKEND_ENV}" "${key}")" ]]; then
        env_set "${BACKEND_ENV}" "${key}" "$(hex_secret)"
    fi
done
if [[ -z "$(env_get "${BACKEND_ENV}" EMAIL_ENCRYPTION_KEYS)" ]]; then
    env_set "${BACKEND_ENV}" EMAIL_ENCRYPTION_KEYS "$(fernet_key)"
fi

declare -A HOST_DEFAULTS=(
    [ENVIRONMENT]=development
    [POSTGRES_HOST]=localhost
    [POSTGRES_PORT]=5432
    [POSTGRES_USER]=postgres
    [POSTGRES_PASSWORD]=postgres
    [POSTGRES_DB]=flowpilot
    [REDIS_URL]=redis://localhost:6379/0
    [STORAGE_BACKEND]=minio
    [S3_BUCKET]=flowpilot-dev
    [S3_ENDPOINT_URL]=http://localhost:9000
    [S3_ACCESS_KEY_ID]=minioadmin
    [S3_SECRET_ACCESS_KEY]=minioadmin
    [AWS_ACCESS_KEY_ID]=minioadmin
    [AWS_SECRET_ACCESS_KEY]=minioadmin
    [SERVICE_ROLE]=web
)
for key in "${!HOST_DEFAULTS[@]}"; do
    [[ -z "$(env_get "${BACKEND_ENV}" "${key}")" ]] && env_set "${BACKEND_ENV}" "${key}" "${HOST_DEFAULTS[${key}]}"
done
env_set "${BACKEND_ENV}" FRONTEND_URL "http://localhost:${FRONTEND_PORT}"

PG_HOST="$(env_get "${BACKEND_ENV}" POSTGRES_HOST)"
if [[ "${PG_HOST}" == "db" || "${PG_HOST}" == "postgres" ]]; then
    env_set "${BACKEND_ENV}" POSTGRES_HOST localhost
fi
S3_URL="$(env_get "${BACKEND_ENV}" S3_ENDPOINT_URL)"
if [[ "${S3_URL}" == *"//minio:"* || "${S3_URL}" == *"//minio/"* ]]; then
    env_set "${BACKEND_ENV}" S3_ENDPOINT_URL http://localhost:9000
fi
ok 'backend/.env validated for host execution.'

FRONTEND_ENV="${FRONTEND_DIR}/.env"
[[ -f "${FRONTEND_ENV}" ]] || cp "${FRONTEND_DIR}/.env.example" "${FRONTEND_ENV}"
env_set "${FRONTEND_ENV}" VITE_API_URL "http://localhost:${API_PORT}/api/v1"

step '4/8  Docker backing services'
cd "${BACKEND_DIR}"
if (( DO_RESET )); then
    docker compose down -v >/dev/null 2>&1 || true
fi
docker compose up -d db redis minio minio-init || die 'docker compose up failed.'

wait_for_port 'PostgreSQL' 5432 120 || die 'PostgreSQL did not open port 5432.'
wait_for_port 'Redis'      6379  60 || die 'Redis did not open port 6379.'
wait_for_port 'MinIO'      9000  90 || die 'MinIO did not open port 9000.'
sleep 2

step '5/8  Database migrations'
if (( SKIP_MIGRATIONS )); then
    warn 'Skipped by request.'
else
    "${VENV_PY}" -m alembic upgrade head || die 'alembic upgrade head failed.'
    HEAD="$("${VENV_PY}" -m alembic current 2>&1 | grep -o '[a-z0-9_]* (head)' || true)"
    ok "Alembic at head: ${HEAD:-unknown}"
fi

step '6/8  Seeding commercial defaults'
"${VENV_PY}" scripts/seed_price_book.py --version 1 >/dev/null 2>&1 && ok "scripts/seed_price_book.py" || warn "scripts/seed_price_book.py (already seeded)"
"${VENV_PY}" scripts/seed_quota_tiers.py >/dev/null 2>&1 && ok "scripts/seed_quota_tiers.py" || warn "scripts/seed_quota_tiers.py (already seeded)"

ADMIN_EMAIL='admin@flowpilot.local'
ADMIN_PASSWORD='FlowPilot!Dev123'
if SEED_JSON="$("${VENV_PY}" scripts/seed_admin.py --json 2>/dev/null)"; then
    ADMIN_EMAIL="$(printf '%s' "${SEED_JSON}"  | python3 -c 'import json,sys; print(json.load(sys.stdin)["email"])' 2>/dev/null || echo "${ADMIN_EMAIL}")"
    ADMIN_PASSWORD="$(printf '%s' "${SEED_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])' 2>/dev/null || echo "${ADMIN_PASSWORD}")"
    ok "Admin account: ${ADMIN_EMAIL}"
fi

step '7/8  Starting application processes'
: > "${PID_FILE}"

setsid "${VENV_PY}" -m uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" --reload \
    > "${LOG_DIR}/api.log" 2>&1 &
API_PID=$!
echo "api=${API_PID}" >> "${PID_FILE}"
ok "API starting (pid ${API_PID}) -> ${LOG_DIR}/api.log"

setsid "${VENV_PY}" -m app.worker --loop all --profile all --log-level INFO \
    > "${LOG_DIR}/worker.log" 2>&1 &
WORKER_PID=$!
echo "worker=${WORKER_PID}" >> "${PID_FILE}"
ok "Worker starting (pid ${WORKER_PID}) -> ${LOG_DIR}/worker.log"

if ! wait_for_port 'API' "${API_PORT}" 90; then
    die 'The API did not bind.'
fi

if (( ! NO_FRONTEND )); then
    if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
        (cd "${FRONTEND_DIR}" && npm install)
    fi
    (cd "${FRONTEND_DIR}" && setsid npm run dev -- --port "${FRONTEND_PORT}" > "${LOG_DIR}/vite.log" 2>&1 &
     echo "vite=$!" >> "${PID_FILE}")
    ok "Vite starting -> ${LOG_DIR}/vite.log"
    wait_for_port 'Frontend' "${FRONTEND_PORT}" 120 || warn 'Vite has not bound yet; check .dev/logs/vite.log'
fi

step '8/8  Ready'
cat <<BANNER

  ${C_DIM}---------------------------------------------------------------${C_RESET}
   ${C_GREEN}FlowPilot AI is running${C_RESET}
  ${C_DIM}---------------------------------------------------------------${C_RESET}
   App          http://localhost:${FRONTEND_PORT}
   API docs     http://localhost:${API_PORT}/docs
   Health       http://localhost:${API_PORT}/api/v1/health
   MinIO        http://localhost:9001   (minioadmin / minioadmin)

   Sign in with
     email      ${C_YELLOW}${ADMIN_EMAIL}${C_RESET}
     password   ${C_YELLOW}${ADMIN_PASSWORD}${C_RESET}

   Stop everything
     ./start_dev.sh --stop
  ${C_DIM}---------------------------------------------------------------${C_RESET}
BANNER