#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
project_dir="$(pwd -P)"
bin_dir="$HOME/.local/bin"
launcher="$bin_dir/tr-update"
if command -v tr-update >/dev/null 2>&1 && [[ "$(command -v tr-update)" != "$launcher" ]]; then
  echo '已有其他 tr-update 指令，未覆蓋；請使用專案內的 ./update.sh。' >&2
  exit 1
fi
mkdir -p "$bin_dir"
if [[ -e "$launcher" || -L "$launcher" ]]; then
  if [[ ! -L "$launcher" || "$(readlink "$launcher")" != "$project_dir/update.sh" ]]; then
    echo "已有 $launcher，未覆蓋；請使用專案內的 ./update.sh。" >&2
    exit 1
  fi
else
  ln -s "$project_dir/update.sh" "$launcher"
fi
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  config="$HOME/.zshrc"
  marker='# transcript-local: add local bin to PATH'
  if ! grep -Fqx "$marker" "$config" 2>/dev/null; then
    printf '\n%s\n%s\n' "$marker" 'export PATH="$HOME/.local/bin:$PATH"' >> "$config"
  fi
fi
echo '已設定 tr-update；重新開啟終端機後可直接使用。'
