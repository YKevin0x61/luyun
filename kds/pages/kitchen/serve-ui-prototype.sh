#!/usr/bin/env bash
# Throwaway kitchen-console UI prototype. Not production.
cd "$(dirname "$0")"
PORT="${PORT:-4178}"
echo "PROTOTYPE — kitchen console looks"
echo "  Open: http://127.0.0.1:${PORT}/kitchen-console.ui-prototype.html?variant=A"
echo "  Variants: A grid / B dense list / C one-bite   Keys: ← →"
echo "  Stop: Ctrl+C"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
