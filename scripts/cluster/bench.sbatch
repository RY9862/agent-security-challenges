#!/bin/bash
# Example Slurm job for clusters. Fill in the placeholders for your site, then:
#   sbatch scripts/cluster/bench.sbatch <1|2|3> <model> <seed-base>
#SBATCH --job-name=agent-ctf-bench
#SBATCH --partition=YOUR_PARTITION
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/%x-%j.out
set -uo pipefail
N=$1; MODEL=$2; BASE=${3:-0}
ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$ROOT"
# A random free port, so two jobs on one node don't collide.
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
export OLLAMA_HOST="127.0.0.1:$PORT"
mkdir -p logs
ollama serve >"logs/ollama-${SLURM_JOB_ID:-local}.log" 2>&1 &
SERVE=$!
trap 'kill $SERVE' EXIT
for _ in $(seq 60); do curl -s "http://$OLLAMA_HOST/api/version" >/dev/null && break; sleep 2; done
# Load the model before the first timed request (large models can take minutes).
curl -s --max-time 1800 "http://$OLLAMA_HOST/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":-1}" >/dev/null
MODELS="$MODEL" SEEDS="$BASE" scripts/bench.sh "$N"
