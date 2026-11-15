#!/usr/bin/env bash
# WP18: real container smoke test — fresh clone, make up, endpoint assertions.
# Works in Git Bash on Windows 11 and Linux CI. Cleans up on exit.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMPDIR_QO="$(mktemp -d)"
PROJECT_NAME="qo-smoke-$$"
BASE_URL=""
PG_PORT=""
PASS=0
FAIL=0

cleanup() {
  echo "--- cleanup ---"
  if [ -d "$TMPDIR_QO" ]; then
    (cd "$TMPDIR_QO" && docker compose -p "$PROJECT_NAME" down -v --remove-orphans 2>/dev/null) || true
    rm -rf "$TMPDIR_QO"
  fi
}
trap cleanup EXIT

echo "=== 1. clone current HEAD to $TMPDIR_QO ==="
git clone "$REPO" "$TMPDIR_QO" --quiet

echo "=== 2. create .env with dummy values ==="
cd "$TMPDIR_QO"
cp .env.example .env

echo "=== 3. find free host ports ==="
find_free_port() {
  local start=$1
  python3 -c "
import socket
for p in range($start, $start + 50):
    try:
        s = socket.socket(); s.bind(('127.0.0.1', p)); s.close(); print(p); break
    except OSError: pass
" 2>/dev/null || python -c "
import socket
for p in range($start, $start + 50):
    try:
        s = socket.socket(); s.bind(('127.0.0.1', p)); s.close(); print(p); break
    except OSError: pass
"
}
QO_API_PORT=$(find_free_port 18001)
QO_PG_PORT=$(find_free_port 19001)
QO_GRAFANA_PORT=$(find_free_port 13000)
QO_PROM_PORT=$(find_free_port 19090)
QO_MCP_PORT=$(find_free_port 18002)
export QO_API_PORT QO_PG_PORT QO_GRAFANA_PORT QO_PROM_PORT QO_MCP_PORT
echo "ports: api=$QO_API_PORT pg=$QO_PG_PORT grafana=$QO_GRAFANA_PORT prom=$QO_PROM_PORT mcp=$QO_MCP_PORT"
BASE_URL="http://127.0.0.1:$QO_API_PORT"

echo "=== 4. docker compose up -d --build ==="
docker compose -p "$PROJECT_NAME" up -d --build api postgres 2>&1 | tail -3

echo "=== 5. wait up to 180s for /healthz ==="
HEALTHY=false
for i in $(seq 1 36); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/healthz" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then HEALTHY=true; echo "healthz OK after $((i*5))s"; break; fi
  sleep 5
done
if [ "$HEALTHY" != "true" ]; then echo "FAIL: /healthz never returned 200"; exit 1; fi

echo "=== 6. endpoint assertions ==="
check() {
  local desc="$1" url="$2" expect_code="$3" expect_content="${4:-}"
  local code body
  code=$(curl -s -o /tmp/smoke_body -w "%{http_code}" "$url" 2>/dev/null || echo "000")
  body=$(cat /tmp/smoke_body 2>/dev/null || echo "")
  local pass=true
  [ "$code" != "$expect_code" ] && pass=false
  if [ -n "$expect_content" ] && ! echo "$body" | grep -q "$expect_content"; then pass=false; fi
  if $pass; then
    echo "  PASS $desc ($code)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL $desc (got $code, expected $expect_code)"
    echo "    body: $(echo "$body" | head -c 200)"
    FAIL=$((FAIL + 1))
  fi
}

check "/healthz"                    "$BASE_URL/healthz"                    200 "OK"
check "/docs"                       "$BASE_URL/docs"                       200
check "/ui/ (index)"                "$BASE_URL/ui/"                        200 "Query Optimizer"
check "/ui (no slash, redirect)"    "$BASE_URL/ui"                         "200\|307"
check "/ui/app.js"                  "$BASE_URL/ui/app.js"                  200
check "/ui/styles.css"              "$BASE_URL/ui/styles.css"              200
check "/ui/api/summary"             "$BASE_URL/ui/api/summary?range=24h"   200
check "/metrics"                    "$BASE_URL/metrics"                    200
check "/openapi.json"               "$BASE_URL/openapi.json"               200

# UI index must reference app.js and styles.css
UI_HTML=$(curl -s "$BASE_URL/ui/" 2>/dev/null)
for asset in app.js styles.css; do
  if echo "$UI_HTML" | grep -q "$asset"; then
    echo "  PASS ui index references $asset"
    PASS=$((PASS + 1))
  else
    echo "  FAIL ui index missing reference to $asset"
    FAIL=$((FAIL + 1))
  fi
done

# /metrics must contain our custom counters
METRICS=$(curl -s "$BASE_URL/metrics" 2>/dev/null)
for metric in qo_sessions_created_total qo_stage_calls_total; do
  if echo "$METRICS" | grep -q "$metric"; then
    echo "  PASS metrics contains $metric"
    PASS=$((PASS + 1))
  else
    echo "  FAIL metrics missing $metric"
    FAIL=$((FAIL + 1))
  fi
done

# Grafana
GRAFANA_URL="http://127.0.0.1:$QO_GRAFANA_PORT/api/health"
gcode=$(curl -s -o /dev/null -w "%{http_code}" "$GRAFANA_URL" 2>/dev/null || echo "000")
if [ "$gcode" = "200" ]; then
  echo "  PASS Grafana /api/health ($gcode)"
  PASS=$((PASS + 1))
else
  echo "  FAIL Grafana /api/health ($gcode)"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "=== SMOKE RESULT: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -gt 0 ]; then
  echo "SMOKE-FAIL"
  exit 1
fi
echo "SMOKE-PASS"
