#!/bin/sh
# One-time setup for a fresh clone: a project-only Python environment (.venv) with the tested library versions,
# so the app works the same whatever other Pythons are installed. Safe to re-run.
set -e
cd "$(dirname "$0")"
PY=""
for candidate in python3 python3.15 python3.14 python3.13 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12 or newer is needed (numpy requires it; on a Mac: brew install python)" >&2
  exit 1
fi
# --upgrade-deps brings pip up to date once, when the environment is created
[ -x .venv/bin/python ] || "$PY" -m venv --upgrade-deps .venv
.venv/bin/python -m pip install --quiet -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Ready ($(.venv/bin/python --version)). Put your Odds API key in .env, then run:"
echo "  .venv/bin/python -m betbot.main"
