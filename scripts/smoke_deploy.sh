#!/usr/bin/env bash
# Post-deploy smoke (WP8): run on the VPS after `make up` with Caddy in front.
# Usage: BASE_URL=https://your-domain ./scripts/smoke_deploy.sh
set -euo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
TOKEN="${API_BEARER_TOKEN:-change-me}"
AUTH=()
if [ "$TOKEN" != "change-me" ]; then AUTH=(-H "Authorization: Bearer $TOKEN"); fi

echo "== 1. healthz =="
curl -fsS "$BASE_URL/healthz" && echo

echo "== 2. create session (spec pipeline) =="
SID=$(curl -fsS -X POST "$BASE_URL/sessions" \
  -H "Content-Type: application/json" -H "Idempotency-Key: smoke-$(date +%s)" \
  "${AUTH[@]}" \
  -d '{"idea":"A tiny habit tracker with streaks"}' | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
echo "session: $SID"

echo "== 3. proxy call (OpenAI-style, mode=safe) =="
curl -fsS -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" -H "x-optimizer-mode: safe" "${AUTH[@]}" \
  -d '{"model":"groq/openai/gpt-oss-20b","messages":[{"role":"user","content":"Reply with exactly: deploy-ok"}]}' \
  | grep -o '"content":"[^"]*"' | head -1

echo "== 4. stats =="
curl -fsS "$BASE_URL/stats"
echo
echo "SMOKE OK"
