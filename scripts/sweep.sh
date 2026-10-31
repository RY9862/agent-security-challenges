#!/usr/bin/env bash
# Run challenges' reference attacks on every NRP model that can call tools, first 10 runs
# per model, then 50, and write attack-success and latency tables for each phase.
#
#   scripts/sweep.sh 1          # one challenge
#   scripts/sweep.sh 1 4 5      # several
#   scripts/sweep.sh all        # all five
#
# With several challenges, the 10-run phase is done for all of them before any 50-run phase,
# so you get a first look at every challenge early.
# Clears the chosen challenges' old results first, so the tables only contain this sweep.
# Environment (all optional):
#   MODELS      models to test               (default: every model on the server that passes check.py)
#   RUNS        runs per model, per phase    (default "10 50")
#   CONDITIONS  attack and/or no_attack      (default "attack")
#   CLEAR       1 = delete old results first (default 1; use 0 to add a phase without deleting)
# Results:  challenges/<challenge>/results/runs-<R>/report.md   (and summary.tsv, raw TSV/traces)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USAGE="Usage: $0 <1-5 ...|all>"

challenge_dir() {
  case "$1" in
    1) echo 01-banking-standing-order ;;
    2) echo 02-slack-phishing-link ;;
    3) echo 03-agentharm-database-audit ;;
    4) echo 04-banking-scheduled-leak ;;
    5) echo 05-workspace-file-delete ;;
    *) return 1 ;;
  esac
}
reference_args() {   # same reference attack each challenge uses in bench.sh
  case "$1" in
    1) echo "--input solutions/reference_v3.txt --user-task user_task_3" ;;
    *) echo "--input solutions/reference.txt" ;;
  esac
}

[ $# -gt 0 ] || { echo "$USAGE" >&2; exit 1; }
if [ "$1" = all ]; then set -- 1 2 3 4 5; fi
for n in "$@"; do challenge_dir "$n" >/dev/null || { echo "$USAGE" >&2; exit 1; }; done
CHALLENGES="$*"

if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/use-nrp.sh" >/dev/null || exit 1
fi
[ -z "${VIRTUAL_ENV:-}" ] && [ -f "$ROOT/.venv/bin/activate" ] && source "$ROOT/.venv/bin/activate"
RUNS="${RUNS:-10 50}"
CONDITIONS="${CONDITIONS:-attack}"

for n in $CHALLENGES; do
  RES="$ROOT/challenges/$(challenge_dir "$n")/results"
  if [ "${CLEAR:-1}" = 1 ]; then
    echo "Clearing old results in $RES"
    rm -rf "$RES" "$ROOT"/logs/ch"$n"-*
  fi
  mkdir -p "$RES"
done

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
echo "Challenges: $CHALLENGES   models: $MODELS   runs per phase: $RUNS"

phase=0
for R in $RUNS; do
  for n in $CHALLENGES; do
    D=$(challenge_dir "$n"); read -r -a ARGS <<< "$(reference_args "$n")"
    RES="$ROOT/challenges/$D/results"; OUT="$RES/runs-$R"; mkdir -p "$OUT/logs"
    echo "$MODELS" | tr ' ' '\n' > "$RES/models.txt"
    for m in $MODELS; do
      log="$OUT/logs/${m//[:\/]/_}.log"
      echo "== challenge $n  $R runs  model $m  -> $log"
      # seeds: phase 1 uses 0..R-1, phase 2 uses 1000.., so the two phases are separate samples
      (cd "$ROOT/challenges/$D" && python harness.py --model "$m" --base-url "$BASE_URL" \
          --temperature 0.7 --runs "$R" --seed-base $((phase * 1000)) --timeout-s 900 \
          --conditions "${CONDITIONS// /,}" --out "$OUT" "${ARGS[@]}") 2>&1 \
        | tee "$log" | grep --line-buffered -E "^(attack|no_attack) +run" | sed 's/^/   /' || true
    done
    echo; python3 "$ROOT/scripts/stats.py" "$OUT"; echo
  done
  phase=$((phase + 1))
done
echo "Done. Reports:"
for n in $CHALLENGES; do for R in $RUNS; do
  echo "   challenges/$(challenge_dir "$n")/results/runs-$R/report.md"
done; done
