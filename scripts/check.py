"""Check that the model server works before playing: the model is listed and it makes
a tool call. Works with a local Ollama or a hosted gateway (e.g. after
`source scripts/use-nrp.sh`).

  python3 scripts/check.py              # checks $MODEL (default qwen3:14b)
  python3 scripts/check.py gpt-oss kimi # checks several
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_balance",
        "description": "Get the current balance of the user's bank account.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


def main() -> int:
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").removeprefix("http://")
    base_url = os.environ.get("BASE_URL") or f"http://{host}/v1"
    models = sys.argv[1:] or [os.environ.get("MODEL", "qwen3:14b")]
    client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
                    max_retries=3, timeout=900)
    print(f"Server: {base_url}")

    try:
        ids = sorted(m.id for m in client.models.list().data)
    except Exception as e:
        print(f"[FAIL] cannot list models: {type(e).__name__}: {e}")
        print("       Local: is `scripts/serve.sh` running?  Hosted: did you `source scripts/use-nrp.sh`?")
        return 1
    print("Models:", ", ".join(ids))

    extra = {"cache_salt": os.environ["NRP_CACHE_SALT"]} if os.environ.get("NRP_CACHE_SALT") else {}
    ok = True
    for m in models:
        if m not in ids:
            print(f"[FAIL] {m}: not on this server")
            ok = False
            continue
        try:
            resp = client.chat.completions.create(
                model=m, messages=[{"role": "user", "content": "What is my bank balance? Use the tool."}],
                tools=PROBE_TOOL, temperature=0.7, seed=0, extra_body=extra)
        except Exception as e:
            print(f"[FAIL] {m}: {type(e).__name__}: {e}")
            ok = False
            continue
        names = [c.function.name for c in (resp.choices[0].message.tool_calls or [])]
        good = "get_balance" in names
        ok &= good
        print(f"[{'OK' if good else 'FAIL'}] {m}: tool calls {names or 'none'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
