#!/usr/bin/env bash
# macOS / Linux development setup for LTX Desktop
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PNPM_PROJECT="$SCRIPT_DIR/pnpm-project.sh"

# ── Pre-checks ──────────────────────────────────────────────────────
command -v node  >/dev/null 2>&1 || fail "node not found — install Node.js 18+"
command -v uv    >/dev/null 2>&1 || fail "uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
[ -x "$PNPM_PROJECT" ] || fail "project pnpm wrapper not found: $PNPM_PROJECT"
ok "node $(node -v)"
ok "pnpm $("$PNPM_PROJECT" --version)"
ok "uv   $(uv --version)"

# ── pnpm install ────────────────────────────────────────────────────
echo ""
echo "Installing Node dependencies..."
cd "$PROJECT_DIR"
"$PNPM_PROJECT" install
ok "pnpm install complete"

# ── uv sync ─────────────────────────────────────────────────────────
echo ""
echo "Setting up Python backend venv..."
cd "$PROJECT_DIR/backend"
uv sync --extra dev
ok "uv sync complete"

# Verify torch + accelerator
echo ""
if [ "$(uname -s)" = "Darwin" ]; then
  echo "Verifying PyTorch MPS support..."
  .venv/bin/python -c "import torch; mps=hasattr(torch.backends,'mps') and torch.backends.mps.is_available(); print(f'MPS available: {mps}')" || true
else
  echo "Verifying PyTorch CUDA support..."
  .venv/bin/python -c "import torch; cuda=torch.cuda.is_available(); print(f'CUDA available: {cuda}')" || true
fi

# ── ffmpeg check ────────────────────────────────────────────────────
echo ""
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "⚠  ffmpeg not found — install with: brew install ffmpeg"
  else
    echo "⚠  ffmpeg not found — install with: sudo apt install ffmpeg  (or sudo dnf install ffmpeg)"
  fi
  echo "   (imageio-ffmpeg bundled binary will be used as fallback)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete! Run the app with:  ./scripts/pnpm-project.sh dev"
echo "  Debug mode (with debugpy):         ./scripts/pnpm-project.sh dev:debug"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
