#!/usr/bin/env bash
# Play one challenge against a local model, with a throwaway flag.
# Works on a scratch copy, so the committed config.json is never changed.
#
#   scripts/play.sh <1|2|3>      1 = banking   2 = slack   3 = database audit
#
# Environment:
#   MODEL      model name            (default qwen3:14b)
#   BASE_URL   OpenAI-compatible URL (default http://$OLLAMA_HOST/v1, i.e. local Ollama)
set -eu
N="${1:-}"
MODEL="${MODEL:-qwen3:14b}"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"; HOST="${HOST#http://}"
BASE_URL="${BASE_URL:-http://$HOST/v1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$ROOT/.venv/bin/activate" ] && source "$ROOT/.venv/bin/activate"

case "$N" in
  1) DIR="$ROOT/challenges/01-banking-standing-order" ;;
  2) DIR="$ROOT/challenges/02-slack-phishing-link" ;;
  3) DIR="$ROOT/challenges/03-agentharm-database-audit" ;;
  *) echo "Usage: $0 <1|2|3>   1=banking  2=slack  3=database audit" >&2; exit 1 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$DIR/challenge" "$WORK/challenge"

python3 - "$WORK/challenge/config.json" "$MODEL" "$BASE_URL" <<'PY'
import json, sys
path, model, base_url = sys.argv[1:4]
cfg = json.load(open(path))
cfg.update(model=model, base_url=base_url, seed=None)   # unseeded, like a real attempt
json.dump(cfg, open(path, "w"), indent=2)
PY

echo "practice{local_flag}" > "$WORK/flag"
echo "=== Challenge $N   (model: $MODEL) ==="
echo "Read the briefing, type your input, then press Ctrl-D (Ctrl-Z then Enter on Windows/WSL terminals that need it). Ctrl-C quits."
echo
cd "$WORK/challenge"
python3 run --flag-path "$WORK/flag"
