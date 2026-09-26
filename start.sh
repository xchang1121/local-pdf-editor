#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -n "${PYTHON:-}" ]]; then
  exec "$PYTHON" run.py "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 run.py "$@"
elif command -v python >/dev/null 2>&1; then
  exec python run.py "$@"
else
  echo 'Python not found. Install Python 3.10-3.13, then try again.' >&2
  exit 1
fi
