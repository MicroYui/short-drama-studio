#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"

cd "${project_root}"
export npm_config_registry="https://packagefeedproxy.microsoft.io/npm/"
exec npm exec --yes --package=pnpm@10.30.3 -- pnpm "$@"
