#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1 || [[ ! -x .venv/bin/transcript-local ]]; then
  echo '尚未完成安裝，請先執行 ./setup.sh' >&2
  exit 1
fi
export TRANSCRIPT_LOCAL_PROJECT="$PWD"
# Launch installed code without uv network checks: cached models work offline.
exec .venv/bin/transcript-local "$@"
