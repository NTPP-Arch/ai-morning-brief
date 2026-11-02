#!/usr/bin/env bash
# 生成当日目录骨架：sources/<今天>/ + briefs/<今天>.md（从模板）
# 用法: ./scripts/new-day.sh [YYYY-MM-DD]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

day="${1:-$(date +%F)}"
mkdir -p "sources/$day" briefs notes

if [[ -f "briefs/$day.md" ]]; then
  echo "briefs/$day.md 已存在，跳过"
else
  sed "s/{{DATE}}/$day/g" templates/早报模板.md > "briefs/$day.md"
  echo "已生成 briefs/$day.md"
fi

echo "sources/$day/ 就绪"
echo "下一步：把素材放进去，然后"
echo "  ./scripts/ask.sh codex \"清洗 sources/$day 的素材\""
echo "  ./scripts/ask.sh deepseek \"把素材压成 40 条以内要点\""
echo "  ./scripts/ask.sh claude \"按模板成稿到 briefs/$day.md\""
