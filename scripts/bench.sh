#!/usr/bin/env bash
# Repeated-run acceptance test against models on NRP: attack and no-attack, 10 runs per block.
#
#   scripts/bench.sh <1-5> [--input FILE] [--conditions attack,no_attack]
#
# Uses NRP by default. If BASE_URL isn't set, this sources scripts/use-nrp.sh for you
# (needs your key in ~/.config/agent-security-challenges/nrp_key, see the README).
#
# Environment:
#   MODELS   space-separated NRP model names  (default: $MODEL from use-nrp.sh, i.e. gpt-oss)
#   SEEDS    block start seeds                (default "0" = seeds 0-9; "0 10 20 30 40 50" = 0-59)
#   BASE_URL OpenAI-compatible URL            (set it yourself only to use a different server,
#                                              e.g. a local Ollama at http://127.0.0.1:11434/v1)
# Runs go one at a time, so a whole benchmark only ever has one request open on NRP.
# Results: challenges/<challenge>/results/*.tsv  (summarize with scripts/summarize.py)
set -euo pipefail
N="${1:-}"; shift || true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/use-nrp.sh" >/dev/null || exit 1
fi
[ -z "${VIRTUAL_ENV:-}" ] && [ -f "$ROOT/.venv/bin/activate" ] && source "$ROOT/.venv/bin/activate"
MODELS="${MODELS:-${MODEL:-gpt-oss}}"
SEEDS="${SEEDS:-0}"

case "$N" in
  1) D=01-banking-standing-order;   DEF=(--input solutions/reference_v3.txt --user-task user_task_3) ;;
  2) D=02-slack-phishing-link;      DEF=(--input solutions/reference.txt) ;;
  3) D=03-agentharm-database-audit; DEF=(--input solutions/reference.txt) ;;
  4) D=04-banking-scheduled-leak;   DEF=(--input solutions/reference.txt) ;;
  5) D=05-workspace-file-delete;    DEF=(--input solutions/reference.txt) ;;
  *) echo "Usage: $0 <1-5> [harness options]" >&2; exit 1 ;;
esac
[ $# -gt 0 ] && DEF=("$@")           # your own options replace the defaults

echo "Server: $BASE_URL   models: $MODELS   seed blocks: $SEEDS"
# Fail fast if a model name is wrong or the key doesn't work, before any timed runs.
python3 "$ROOT/scripts/check.py" $MODELS | sed 's/^/   /' || {
  echo "Model check failed; nothing was run. Model names must match the server's list above." >&2; exit 1; }

mkdir -p "$ROOT/logs"
for m in $MODELS; do for b in $SEEDS; do
  log="$ROOT/logs/ch$N-${m//[:\/]/_}-s$b.log"
  echo "== challenge $N  model $m  seeds $b-$((b+9))  -> $log"
  case "$m" in kimi|qwen3|qwen3-small|glm-5|deepseek-v4-flash|minimax) T=900 ;; *) T=300 ;; esac
  (cd "$ROOT/challenges/$D" && python harness.py --model "$m" --base-url "$BASE_URL" \
      --temperature 0.7 --runs 10 --seed-base "$b" --timeout-s "$T" "${DEF[@]}") >"$log" 2>&1 \
    || echo "   (acceptance test did not pass; see log)"
  tail -n 4 "$log" | sed 's/^/   /'
done; done
