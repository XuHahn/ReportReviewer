#!/bin/sh
set -eu
PORT="${1:-4174}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 -m http.server "$PORT" --directory "$ROOT"
