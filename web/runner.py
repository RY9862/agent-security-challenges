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
import importlib.machinery
import importlib.util
import json
import re
import sys
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


def run(chal: Path, payload: dict) -> dict:
    agent, grader = _load(chal)
    # A run can take minutes on a busy shared model. Report each tool call on stderr
    # as it happens so the page can show live progress. Every agent.py calls
    # be.run_tool through the module, so wrapping it here changes nothing else.
    real_run_tool = agent.be.run_tool

    def run_tool(env, name, args):
        out, err = real_run_tool(env, name, args)
        sys.__stderr__.write(PROGRESS + json.dumps({"name": name, "args": args, "error": bool(err)},
                                                   default=str) + "\n")
        sys.__stderr__.flush()
        return out, err

    agent.be.run_tool = run_tool
    cfg = agent.AgentConfig(**payload["config"])
    g = grader.grade(payload["text"], cfg)
    r = g.run
    return {
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
