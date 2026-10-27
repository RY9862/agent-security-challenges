# Point the scripts at the National Research Platform (NRP) LLM gateway instead of a
# local Ollama, so you can play without a GPU.     source scripts/use-nrp.sh
#
# One-time: get a key at https://nrp.ai/llmtoken (your account must be in a namespace
# with LLM access), then store it OUTSIDE this repo:
#   mkdir -p ~/.config/agent-security-challenges && chmod 700 ~/.config/agent-security-challenges
#   nano ~/.config/agent-security-challenges/nrp_key     # paste the key, save
#   chmod 600 ~/.config/agent-security-challenges/nrp_key
# (Or export NRP_API_KEY yourself before sourcing this file.)

_ASC_CONF="${ASC_CONF:-$HOME/.config/agent-security-challenges}"

if [ -z "${NRP_API_KEY:-}" ]; then
  if [ ! -s "$_ASC_CONF/nrp_key" ]; then
    echo "use-nrp.sh: no key. Put it in $_ASC_CONF/nrp_key (see the top of this file)." >&2
    unset _ASC_CONF; return 1 2>/dev/null || exit 1
  fi
  NRP_API_KEY="$(tr -d '[:space:]' < "$_ASC_CONF/nrp_key")"
fi

export BASE_URL="https://ellm.nrp-nautilus.io/v1"
export OPENAI_BASE_URL="$BASE_URL"
export OPENAI_API_KEY="$NRP_API_KEY"
export MODEL="${MODEL:-gpt-oss}"       # fast and good at tools; others: gemma, qwen3, kimi
export MODELS="${MODELS:-$MODEL}"

# A private cache salt keeps your prompts and responses out of the gateway cache other
# users share. URL-safe base64: the gateway rejects '/', '@' and '\'.
mkdir -p "$_ASC_CONF" && chmod 700 "$_ASC_CONF"
if [ ! -s "$_ASC_CONF/nrp_cache_salt" ]; then
  (umask 077; python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(48)).decode())' \
     > "$_ASC_CONF/nrp_cache_salt")
fi
export NRP_CACHE_SALT="$(tr -d '[:space:]' < "$_ASC_CONF/nrp_cache_salt")"

# Activate the repo venv (created by scripts/setup.sh) so harness.py and check.py
# find their packages when run directly, not only through play.sh / bench.sh.
_ASC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$_ASC_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$_ASC_ROOT/.venv/bin/activate"
elif [ ! -f "$_ASC_ROOT/.venv/bin/activate" ]; then
  echo "use-nrp.sh: no .venv yet; run scripts/setup.sh first." >&2
fi
unset _ASC_ROOT

echo "Using NRP: $BASE_URL  model=$MODEL"
echo "Check it:   python3 scripts/check.py      Then play:  scripts/play.sh 1"
unset _ASC_CONF
