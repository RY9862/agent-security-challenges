#!/usr/bin/env bash
# Start a local Ollama (if one isn't already running) and download a model.
#   scripts/serve.sh [model]          default: qwen3:14b
# Environment:
#   OLLAMA_HOST    host:port to serve on   (default 127.0.0.1:11434)
#   OLLAMA_MODELS  where models are stored (default: Ollama's own default)
set -eu
MODEL="${1:-qwen3:14b}"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"; HOST="${HOST#http://}"
export OLLAMA_HOST="$HOST"
LOG="${TMPDIR:-/tmp}/ollama-$(id -u).log"

command -v ollama >/dev/null 2>&1 || { echo "ollama not found; see scripts/setup.sh" >&2; exit 1; }

up() { curl -sf "http://$HOST/api/version" >/dev/null 2>&1; }
if ! up; then
  echo "Starting ollama serve on $HOST (log: $LOG) ..."
  nohup ollama serve >"$LOG" 2>&1 &
  for _ in $(seq 1 60); do up && break; sleep 1; done
fi
up || { echo "Ollama did not start. Check $LOG" >&2; exit 1; }

echo "Ollama $(curl -s "http://$HOST/api/version") at http://$HOST"
echo "Downloading $MODEL (no-op if already present) ..."
ollama pull "$MODEL"

echo "Loading $MODEL into memory ..."
curl -s --max-time 1800 "http://$HOST/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false}" >/dev/null
ollama ps
echo
echo "Ready. Play with:  MODEL=$MODEL scripts/play.sh <1-5>"
