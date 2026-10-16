# scripts/smoke_public.sh（新建）
set -e
BASE="${PUBLIC_BASE:?set PUBLIC_BASE=https://...}"
curl -sf "$BASE/api/health" | grep -q '"status":"ok"'
curl -sf "$BASE/" | grep -q "<div id=\"root\""
echo PUBLIC_SMOKE_OK
