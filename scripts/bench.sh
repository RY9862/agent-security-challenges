#!/usr/bin/env bash
# Repeated-run acceptance test on one machine: attack and no-attack, 10 runs per block.
#
#   scripts/bench.sh <1|2|3> [--input FILE] [--conditions attack,no_attack]
#
# Environment:
#   MODELS   space-separated models   (default qwen3:14b)
#   SEEDS    block start seeds        (default "0" = seeds 0-9; "0 10 20 30 40 50" = 0-59)
#   BASE_URL OpenAI-compatible URL    (default local Ollama)
# Results: challenges/<challenge>/results/*.tsv  (summarize with scripts/summarize.py)
set -eu
N="${1:-}"; shift || true
MODELS="${MODELS:-qwen3:14b}"
SEEDS="${SEEDS:-0}"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"; HOST="${HOST#http://}"
BASE_URL="${BASE_URL:-http://$HOST/v1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.venv/bin/activate" ] && source "$ROOT/.venv/bin/activate"

case "$N" in
  1) D=01-banking-standing-order;   DEF=(--input solutions/reference_v3.txt --user-task user_task_3) ;;
  2) D=02-slack-phishing-link;      DEF=(--input solutions/reference.txt) ;;
  3) D=03-agentharm-database-audit; DEF=(--input solutions/reference.txt) ;;
  *) echo "Usage: $0 <1|2|3> [harness options]" >&2; exit 1 ;;
esac
[ $# -gt 0 ] && DEF=("$@")           # your own options replace the defaults

mkdir -p "$ROOT/logs"
for m in $MODELS; do for b in $SEEDS; do
  log="$ROOT/logs/ch$N-${m//[:\/]/_}-s$b.log"
  echo "== challenge $N  model $m  seeds $b-$((b+9))  -> $log"
  (cd "$ROOT/challenges/$D" && python harness.py --model "$m" --base-url "$BASE_URL" \
      --temperature 0.7 --runs 10 --seed-base "$b" "${DEF[@]}") >"$log" 2>&1 \
    || echo "   (acceptance test did not pass; see log)"
  tail -n 4 "$log" | sed 's/^/   /'
done; done
