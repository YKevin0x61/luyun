#!/usr/bin/env bash
# Throwaway KDS ops UI prototype. Not production.
cd "$(dirname "$0")"
PORT="${PORT:-4177}"
echo "KDS ops prototype → http://127.0.0.1:${PORT}/kds-ops.html"
echo "Screen-border lab → http://127.0.0.1:${PORT}/screen-border-lab.html"
echo "Variants: ?variant=A|B|C  Surfaces: &surface=hub|kitchen|settings"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
