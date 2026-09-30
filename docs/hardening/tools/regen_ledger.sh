#!/usr/bin/env bash
# Rebuild docs/hardening/COVERAGE.csv from the code (Phase 0 extraction).
# WARNING: this resets every row to "untested". After Phase 1, merge the
# status/last_result/test_file/finding_ids columns back from the old file.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TMP="$(mktemp -d)"
cd "$ROOT/backend"
python3 "$ROOT/docs/hardening/tools/extract_endpoints.py" . \
  | sed -e 's#/api/v1WEBHOOK_PATH#/api/v1/billing/stripe/webhook#' \
        -e 's#/api/v1MULTI_WEBHOOK_PATH#/api/v1/billing/webhooks/{gateway}#' \
        -e 's#reviewv.WS_SUFFIX#review/collab/live#' > "$TMP/endpoints.csv"
python3 "$ROOT/docs/hardening/tools/config_vars.py" > "$TMP/config_vars.tsv"
cd "$ROOT"
python3 docs/hardening/tools/build_ledger.py . "$TMP"
python3 docs/hardening/tools/xcheck_frontend_api.py frontend/src "$TMP/endpoints.csv" | head -1
