#!/usr/bin/env bash
# Run one challenge's reference attack on every NRP model that can call tools, first 10 runs
# per model, then 50, and write attack-success and latency tables for each phase.
#
#   scripts/sweep.sh <1-5>
#
# Clears the challenge's old results first, so the tables only contain this sweep.
# Environment (all optional):
#   MODELS      models to test               (default: every model on the server that passes check.py)
#   RUNS        runs per model, per phase    (default "10 50")
#   CONDITIONS  attack and/or no_attack      (default "attack")
#   CLEAR       1 = delete old results first (default 1; use 0 to add a phase without deleting)
# Results:  challenges/<challenge>/results/runs-<R>/report.md   (and summary.tsv, raw TSV/traces)
set -euo pipefail
N="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "$N" in
  1) D=01-banking-standing-order;   ARGS=(--input solutions/reference_v3.txt --user-task user_task_3) ;;
  2) D=02-slack-phishing-link;      ARGS=(--input solutions/reference.txt) ;;
  3) D=03-agentharm-database-audit; ARGS=(--input solutions/reference.txt) ;;
  4) D=04-banking-scheduled-leak;   ARGS=(--input solutions/reference.txt) ;;
  5) D=05-workspace-file-delete;    ARGS=(--input solutions/reference.txt) ;;
  *) echo "Usage: $0 <1-5>" >&2; exit 1 ;;
esac
if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/use-nrp.sh" >/dev/null || exit 1
fi
[ -z "${VIRTUAL_ENV:-}" ] && [ -f "$ROOT/.venv/bin/activate" ] && source "$ROOT/.venv/bin/activate"
RUNS="${RUNS:-10 50}"
CONDITIONS="${CONDITIONS:-attack}"
RES="$ROOT/challenges/$D/results"

if [ "${CLEAR:-1}" = 1 ]; then
  echo "Clearing old results in $RES"
  rm -rf "$RES" "$ROOT"/logs/ch"$N"-*
fi
mkdir -p "$RES"

if [ -z "${MODELS:-}" ]; then
  echo "Finding models on $BASE_URL that can call tools (one quick test call each)..."
  ALL=$(python3 -c 'import os; from openai import OpenAI
c = OpenAI(base_url=os.environ["BASE_URL"], api_key=os.environ.get("OPENAI_API_KEY", "x"))
print(" ".join(sorted(m.id for m in c.models.list().data)))')
  MODELS=""
  for m in $ALL; do
    if python3 "$ROOT/scripts/check.py" "$m" >/dev/null 2>&1; then
      echo "   OK    $m"; MODELS="$MODELS $m"
    else
      echo "   skip  $m (no tool call)"
    fi
  done
else
  python3 "$ROOT/scripts/check.py" $MODELS | sed 's/^/   /'
fi
MODELS=$(echo $MODELS)
[ -n "$MODELS" ] || { echo "No usable models." >&2; exit 1; }
echo "$MODELS" | tr ' ' '\n' > "$RES/models.txt"
echo "Models: $MODELS"

phase=0
for R in $RUNS; do
  OUT="$RES/runs-$R"; mkdir -p "$OUT/logs"
  for m in $MODELS; do
    log="$OUT/logs/${m//[:\/]/_}.log"
    echo "== challenge $N  $R runs  model $m  -> $log"
    # seeds: phase 1 uses 0..R-1, phase 2 uses 1000.., so the two phases are separate samples
    (cd "$ROOT/challenges/$D" && python harness.py --model "$m" --base-url "$BASE_URL" \
        --temperature 0.7 --runs "$R" --seed-base $((phase * 1000)) --timeout-s 900 \
        --conditions "${CONDITIONS// /,}" --out "$OUT" "${ARGS[@]}") 2>&1 \
      | tee "$log" | grep --line-buffered -E "^(attack|no_attack) +run" | sed 's/^/   /' || true
  done
  echo; python3 "$ROOT/scripts/stats.py" "$OUT"; echo
  phase=$((phase + 1))
done
echo "Done. Reports:"
for R in $RUNS; do echo "   $RES/runs-$R/report.md"; done
