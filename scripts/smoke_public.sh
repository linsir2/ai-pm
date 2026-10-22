#!/usr/bin/env bash
# 公开部署的冒烟检查：健康端点 + 首页真的渲染出了 root 节点。
#
# 用法：
#   PUBLIC_BASE=https://example.com bash scripts/smoke_public.sh
set -e
BASE="${PUBLIC_BASE:?set PUBLIC_BASE=https://...}"
curl -sf "$BASE/api/health" | grep -q '"status":"ok"'
curl -sf "$BASE/" | grep -q "<div id=\"root\""
echo PUBLIC_SMOKE_OK
