#!/bin/bash
# Example Slurm job for long benchmarks on NRP. The models run on NRP, so this only needs a
# CPU and network access; no GPU. Use it when a benchmark is too long to keep a terminal open.
# Needs scripts/setup.sh done and your key in ~/.config/agent-security-challenges/nrp_key.
# Fill in the placeholders for your site, then:
#   sbatch scripts/cluster/bench.sbatch <1-5> <model> <seed-base>
#SBATCH --job-name=agent-ctf-bench
#SBATCH --partition=YOUR_PARTITION
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x-%j.out
set -uo pipefail
N=$1; MODEL=$2; BASE=${3:-0}
ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$ROOT"
mkdir -p logs
MODELS="$MODEL" SEEDS="$BASE" scripts/bench.sh "$N"
