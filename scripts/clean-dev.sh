#!/usr/bin/env bash
# Reset the dev env to a clean state (deps removed + reinstalled).
set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Removing build artifacts and dependencies..."
rm -rf node_modules dist dist-electron release backend/.venv
find . -name '*.tsbuildinfo' -not -path '*/node_modules/*' -delete
find backend -type d -name '__pycache__' -exec rm -rf {} +
ok "workspace cleaned"

echo ""
echo "Reinstalling Node dependencies..."
"$SCRIPT_DIR/pnpm-project.sh" install
ok "pnpm install complete"

echo ""
echo "Reinstalling Python backend venv..."
cd "$PROJECT_DIR/backend"
uv sync --extra dev
ok "uv sync complete"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Clean complete! Run the app with:  ./scripts/pnpm-project.sh dev"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
