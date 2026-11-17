#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1 || [[ ! -x .venv/bin/transcript-local ]]; then
  echo '尚未完成安裝，請先執行 ./setup.sh' >&2
  exit 1
fi
export TRANSCRIPT_LOCAL_PROJECT="$PWD"
# Always run this checkout's source, with or without arguments. The copy installed in
# .venv can be older, and its supervisor would start the wrong version.
# No uv network checks here: cached models work offline.
export PYTHONPATH="$PWD/src"
exec .venv/bin/python -m transcript_local.cli "$@"
