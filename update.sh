#!/bin/bash
set -euo pipefail
script="$0"
if [[ -L "$script" ]]; then script="$(readlink "$script")"; fi
if [[ ! -f "$script" ]]; then echo '找不到專案，請重新執行安裝更新指令。' >&2; exit 1; fi
cd "$(dirname "$script")"
echo '更新 Transcript Local；請先停止正在執行的程式。'
git pull --ff-only
./setup.sh
