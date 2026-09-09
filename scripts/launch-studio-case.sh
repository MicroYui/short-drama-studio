#!/usr/bin/env bash
set -euo pipefail
studio_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
studio_app="$studio_root/output/product-delivery/package/mac-arm64/Short Drama Studio.app/Contents/MacOS/Short Drama Studio"
studio_data="$studio_root/output/product-delivery/native-case-data"
if [[ ! -x "$studio_app" || ! -f "$studio_data/short-drama.sqlite3" ]]; then
  echo "本地应用或案例副本未找到，请先按 README 完成构建与案例准备。" >&2
  exit 1
fi
export STUDIO_APP_DATA_DIR="$studio_data"
exec "$studio_app" "$@"
