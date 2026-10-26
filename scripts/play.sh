#!/usr/bin/env bash
# Play one challenge against a local model, with a throwaway flag.
# Works on a scratch copy, so the committed config.json is never changed.
#
#   scripts/play.sh <1|2|3>      1 = banking   2 = slack   3 = database audit
#
# Environment:
#   MODEL      model name            (default qwen3:14b)
#   BASE_URL   OpenAI-compatible URL (default http://$OLLAMA_HOST/v1, i.e. local Ollama)
#   OPENAI_API_KEY  key for a hosted gateway (set by: source scripts/use-nrp.sh)
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

umask 077   # the scratch config may hold an API key
python3 - "$WORK/challenge/config.json" "$MODEL" "$BASE_URL" <<'PY'
import json, os, sys
path, model, base_url = sys.argv[1:4]
cfg = json.load(open(path))
cfg.update(model=model, base_url=base_url, seed=None)   # unseeded, like a real attempt
if os.environ.get("OPENAI_API_KEY"):        # hosted gateway (key from the environment, never argv)
    cfg.update(api_key=os.environ["OPENAI_API_KEY"], max_retries=8)
if os.environ.get("NRP_CACHE_SALT"):
    cfg.setdefault("extra_body", {})["cache_salt"] = os.environ["NRP_CACHE_SALT"]
if model in ("kimi", "qwen3", "qwen3-small", "glm-5", "deepseek-v4-flash", "minimax"):
    cfg["timeout_s"] = 900.0                 # always-thinking hosted models are slow
json.dump(cfg, open(path, "w"), indent=2)
PY

echo "practice{local_flag}" > "$WORK/flag"
echo "=== Challenge $N   (model: $MODEL) ==="
echo "Read the briefing, type your input, then press Ctrl-D (Ctrl-Z then Enter on Windows/WSL terminals that need it). Ctrl-C quits."
echo
cd "$WORK/challenge"
python3 run --flag-path "$WORK/flag"
