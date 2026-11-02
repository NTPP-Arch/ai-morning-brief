#!/usr/bin/env bash
# 三模型统一调用入口
# 用法: ./scripts/ask.sh <claude|codex|deepseek> "你的问题"
#   例: ./scripts/ask.sh codex "把 sources/2026-11-02/*.md 去重并转成要点清单"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi

who="${1:-}"; shift || true
prompt="${*:-}"

if [[ -z "$who" || -z "$prompt" ]]; then
  sed -n '2,4p' "$0"
  exit 2
fi

case "$who" in
  claude)
    command -v claude >/dev/null || { echo "未找到 claude CLI"; exit 127; }
    claude -p "$prompt"
    ;;
  codex)
    command -v codex >/dev/null || { echo "未找到 codex CLI"; exit 127; }
    codex exec "$prompt"
    ;;
  deepseek)
    : "${DEEPSEEK_API_KEY:?请先在 .env 里填 DEEPSEEK_API_KEY}"
    base="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
    model="${DEEPSEEK_MODEL:-deepseek-chat}"
    python - "$base" "$model" "$prompt" <<'PY'
import json, os, sys, urllib.request
base, model, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({
    "model": model,
    "messages": [
        {"role": "system", "content": "你是中文早报的摘要编辑。只做压缩与结构化，不添加原文没有的事实。"},
        {"role": "user", "content": prompt},
    ],
    "stream": False,
}).encode()
req = urllib.request.Request(
    base.rstrip("/") + "/chat/completions",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"],
    },
)
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    print(data["choices"][0]["message"]["content"])
except Exception as e:
    print("DeepSeek 调用失败:", e, file=sys.stderr)
    sys.exit(1)
PY
    ;;
  *)
    echo "未知对象: $who（可选 claude | codex | deepseek）"
    exit 2
    ;;
esac
