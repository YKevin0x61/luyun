#!/usr/bin/env bash
# PROTOTYPE — throwaway. Serves kds/prototype-hub-settings on :8765
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/kds/prototype-hub-settings"
PORT="${PORT:-8765}"

echo "PROTOTYPE KDS Hub + Settings"
echo "  Open: http://127.0.0.1:${PORT}/?variant=W&page=hub"
echo "  Verdict: Hub=C + Settings=B (variant W)"
echo "  Switch variants with the bottom bar or ← →"
echo "  Stop: Ctrl+C"
echo
cd "$DIR"
exec python3 -m http.server "$PORT"
