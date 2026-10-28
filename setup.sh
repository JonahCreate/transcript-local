#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo '第一版正式支援 Apple Silicon Mac。其他平台尚未驗證。' >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo '請先安裝 uv：brew install uv（Homebrew：https://brew.sh）' >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo '請先安裝 FFmpeg：brew install ffmpeg' >&2
  exit 1
fi
uv sync --frozen --no-editable --reinstall-package transcript-local --python 3.12
.venv/bin/transcript-local doctor
echo '安裝完成。執行 ./start.sh，首次使用請在網頁選擇並下載模型。'
