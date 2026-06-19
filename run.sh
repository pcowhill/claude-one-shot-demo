#!/usr/bin/env bash
#
# One command to launch the live operations dashboard. Requires only Python 3.11+.
# It creates a virtual environment on first run, installs the (small) runtime dependencies,
# and starts the server on http://127.0.0.1:8000.
#
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv) ..."
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo
echo "  Roster-5 Allocation Engine — live dashboard"
echo "  Open http://127.0.0.1:8000 in your browser. Press Ctrl-C to stop."
echo
exec python -m app
