#!/usr/bin/env bash
# One-time setup: Python environment + check that Ollama is installed.
#   scripts/setup.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "need Python 3.10+"' \
  || { echo "Python 3.10 or newer is required (set PYTHON=/path/to/python3.x)." >&2; exit 1; }

if [ ! -d .venv ]; then
  echo "Creating .venv ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "Python packages installed."

if command -v ollama >/dev/null 2>&1; then
  echo "Ollama found: $(ollama --version 2>&1 | tail -n 1)"
else
  echo
  echo "Ollama is not installed. Install it from https://ollama.com/download"
  echo "(or unpack a release from https://github.com/ollama/ollama/releases into a folder on your PATH)."
fi

echo
echo "Offline self-test (no model needed):"
for d in challenges/*/; do
  python -m pytest -q "$d/tests" 2>&1 | tail -n 1 | sed "s|^|  $(basename "$d"): |"
done
echo
echo "Next:  scripts/serve.sh      # start Ollama and download the model"
echo "Then:  scripts/play.sh 1     # play challenge 1 (or 2, 3)"
