#!/bin/bash
set -euo pipefail
script="$0"
if [[ -L "$script" ]]; then script="$(readlink "$script")"; fi
cd "$(dirname "$script")"
exec ./start.sh update
