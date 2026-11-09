"""Run one challenge in its own process and print JSON. Used by web/server.py.

Every challenge has modules with the same names (agent, grader, banking_env, ...), so
they can't share one interpreter. The server starts this script once per attempt:

    python3 -I runner.py meta <challenge-folder>      -> briefing + input limit
    python3 -I runner.py run  <challenge-folder>      -> stdin: {"text", "config"}
                                                         stdout: the grade as JSON

The config (model, base_url, api_key, ...) comes from the server on stdin, never from
argv, so the key doesn't show in `ps`. The player only ever controls "text".
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.machinery
import importlib.util
import json
import re
import sys
import time
from pathlib import Path


def _load(chal: Path):
    sys.path.insert(0, str(chal / "challenge"))
    import agent  # noqa: E402
    import grader  # noqa: E402
    return agent, grader


def meta(chal: Path) -> dict:
    _, grader = _load(chal)
    loader = importlib.machinery.SourceFileLoader("challenge_run", str(chal / "challenge" / "run"))
    spec = importlib.util.spec_from_loader("challenge_run", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)   # safe: the CLI only runs under `if __name__ == "__main__"`
    cfg = json.loads((chal / "challenge" / "config.json").read_text())
    text = mod.briefing(cfg.get("user_task")) if hasattr(mod, "briefing") else mod.BRIEFING

    # The terminal briefing starts with "=== slug: title ===" and ends with
    # "Type your ... then Ctrl-D:". The web page shows the title and the prompt itself.
    lines = text.rstrip("\n").split("\n")
    title = ""
    if lines and lines[0].startswith("==="):
        head = lines.pop(0).strip("= ")
        title = head.split(": ", 1)[1] if ": " in head else head
    prompt = ""
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("Type ")):
        last = lines.pop().strip()
        if last.startswith("Type "):
            # "Type your transaction subject (max 2000 chars), then Ctrl-D:" -> the input label
            prompt = re.sub(r",?\s*then Ctrl-D:?\s*$", "", last)
    return {"title": title, "briefing": "\n".join(lines), "prompt": prompt,
            "max_input_chars": grader.MAX_INPUT_CHARS}


PROGRESS = "@@progress "


def folder_hash(folder: Path) -> str:
    """Short hash of every file in a challenge's code folder. Computed when the run starts,
    because the code is loaded fresh for every run: if a challenge is edited while the
    server is up, the next run uses the new code and records the new version."""
    h = hashlib.sha256()
    for f in sorted(folder.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            h.update(str(f.relative_to(folder)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def _token_totals(model_calls: list[dict]) -> dict:
    usages = [c.get("usage") or {} for c in model_calls]
    return {
        "input": sum(u.get("prompt_tokens") or 0 for u in usages),
        "output": sum(u.get("completion_tokens") or 0 for u in usages),
        "reasoning": sum((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                         for u in usages),
    }


# Everything a run collects is also streamed to the server on stderr as it happens
# ("@@partial <json>" lines). If the run fails partway (the model server errors on call 5
# of 7, the run hits the time limit, or this process crashes), the server still has every
# model response and tool result up to that point.
PARTIAL = "@@partial "


def emit(kind: str, data) -> None:
    sys.__stderr__.write(PARTIAL + json.dumps({"kind": kind, "data": data}, default=str) + "\n")
    sys.__stderr__.flush()


def run(chal: Path, payload: dict) -> dict:
    version = folder_hash(chal / "challenge")
    emit("challenge_version", version)
    agent, grader = _load(chal)
    # A run can take minutes on a busy shared model. Report each tool call on stderr
    # as it happens so the page can show live progress. Every agent.py calls
    # be.run_tool through the module, so wrapping it here changes nothing else.
    real_run_tool = agent.be.run_tool

    def run_tool(env, name, args):
        out, err = real_run_tool(env, name, args)
        emit("tool_call", {"name": name, "args": args, "result": out, "error": err})
        sys.__stderr__.write(PROGRESS + json.dumps({"name": name, "args": args, "error": bool(err)},
                                                   default=str) + "\n")
        sys.__stderr__.flush()
        return out, err

    agent.be.run_tool = run_tool

    # Keep every raw model response for later investigation: token usage (input, output,
    # reasoning), finish reason, and the whole message, including any reasoning text the
    # server returns in extra fields (gpt-oss and qwen3 do). The trace only keeps the
    # visible reply. Same idea as above: wrap the client, leave the challenge code alone.
    model_calls: list[dict] = []
    real_make_client = agent.make_client

    def make_client(cfg):
        client = real_make_client(cfg)
        completions = client.chat.completions
        real_create = completions.create

        def create(**kwargs):
            t0 = time.perf_counter()
            try:
                resp = real_create(**kwargs)
            except Exception as e:  # record the failed call too, then let the run fail as before
                call = {"seconds": round(time.perf_counter() - t0, 2), "error": f"{type(e).__name__}: {e}"}
                model_calls.append(call)
                emit("model_call", call)
                raise
            choice = resp.choices[0]
            call = {
                "seconds": round(time.perf_counter() - t0, 2),
                "usage": resp.usage.model_dump(exclude_none=True) if resp.usage else None,
                "finish_reason": choice.finish_reason,
                "message": choice.message.model_dump(exclude_none=True),
            }
            model_calls.append(call)
            emit("model_call", call)
            return resp

        completions.create = create
        return client

    agent.make_client = make_client
    cfg = agent.AgentConfig(**payload["config"])
    g = grader.grade(payload["text"], cfg)
    r = g.run
    return {
        "challenge_version": version,
        "model_calls": model_calls,
        "tokens": _token_totals(model_calls),
        "call_tokens": getattr(r, "call_tokens", []),
        "passed": bool(g.passed),
        "user_task_done": g.user_task_done,
        "criteria": getattr(g, "criteria", None),
        "final_output": r.final_output,
        "trace": [dataclasses.asdict(s) for s in r.trace],
        "steps_used": r.steps_used,
        "hit_step_limit": r.hit_step_limit,
        "call_seconds": r.call_seconds,
        "served_model": r.served_model,
    }


def main() -> int:
    mode, chal = sys.argv[1], Path(sys.argv[2]).resolve()
    out = sys.stdout
    try:
        # Anything the challenge prints goes to stderr so stdout stays one JSON object.
        with contextlib.redirect_stdout(sys.stderr):
            result = meta(chal) if mode == "meta" else run(chal, json.loads(sys.stdin.read()))
    except Exception as e:  # model server down, timeout, bad config
        out.write(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 3
    out.write(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
