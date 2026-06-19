#!/usr/bin/env bash
#
# Regenerate every committed artifact: diagrams, benchmark charts + JSON, the dashboard
# screenshots, and the self-contained report.html. Run from anywhere.
#
# Requires the dev dependencies:  pip install -r requirements-dev.txt
# Screenshots need a headless Chromium. On a normal machine:  playwright install chromium
# If you already have a Chromium on disk, point at it:  export CHROME_PATH=/path/to/chrome
#
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

echo "1/4  Diagrams (SVG) ..."
python diagrams/generate_diagrams.py

echo "2/4  Benchmarks (charts + JSON) ..."
python benchmarks/run_benchmarks.py

echo "3/4  Dashboard screenshots (headless Chromium) ..."
python scripts/capture_screenshots.py

echo "4/4  Self-contained report.html ..."
python scripts/generate_report.py

echo
echo "Done. See artifacts/, benchmarks/results/, diagrams/, and report.html."
