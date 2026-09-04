#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
managed_registry="https://packagefeedproxy.microsoft.io/npm/"

cd "${project_root}"

package_manager="$({
  node --input-type=commonjs -e '
    const {readFileSync} = require("node:fs");
    const value = JSON.parse(readFileSync("package.json", "utf8")).packageManager;
    if (typeof value !== "string" || !/^pnpm@[^@]+$/.test(value)) process.exit(1);
    process.stdout.write(value);
  '
} 2>/dev/null)" || {
  echo "ERROR: package.json must pin packageManager as pnpm@<version>." >&2
  exit 1
}

# npm obeys the managed registry; Corepack does not inherit .npmrc. Prefer the
# already-populated npm exec cache so normal commands stay fully offline. A
# cache miss may fetch only the pinned pnpm package from the managed feed.
export npm_config_registry="${managed_registry}"
export npm_config_replace_registry_host="always"

cached_pnpm="$({
  npm exec --offline --yes --package="${package_manager}" -- \
    sh -c 'command -v pnpm'
} 2>/dev/null)" || true

if [ -n "${cached_pnpm}" ] && [ -x "${cached_pnpm}" ]; then
  exec "${cached_pnpm}" "$@"
fi

exec npm exec --yes --package="${package_manager}" -- pnpm "$@"
