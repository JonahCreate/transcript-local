#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(git branch --show-current)" != main ]]; then
  echo "請先切換到 main 分支。"
  exit 1
fi
echo "以下是將發布的變更"
git status --short
git diff --stat
git diff --cached --stat
if [[ -n "$(git ls-files --others --exclude-standard -- . ":(exclude)publish.sh")" ]]; then
  echo "有未追蹤檔案。請先檢查並明確加入 Git，避免發布私人資料。"
  git ls-files --others --exclude-standard -- . ":(exclude)publish.sh"
  exit 1
fi
read -r -p "確認提交已追蹤的變更並推送到 GitHub？ [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || exit 0
git add -u
git add -- publish.sh
if ! git diff --cached --quiet; then
  git commit -m "Update IHT development version"
fi
git push origin main
echo "已推送到 GitHub。"
