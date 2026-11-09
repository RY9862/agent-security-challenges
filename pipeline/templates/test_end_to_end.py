"""Offline end-to-end tests for a generated challenge, with a scripted 'obedient' fake model.

The fake model replays the benchmark's own ground-truth tool calls for the user task, and,
if it sees the reference solution in a tool result, the injection task's calls as well. So
the reference solution gets the flag only if it reaches the agent through the injection point
and the grader recognises the attacker's goal. No GPU or model needed.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "tests" / "upstream_expected.json").read_text())
REFERENCE = (ROOT / "solutions" / "reference.txt").read_text()
sys.path.insert(0, str(ROOT / "challenge"))
from agent import AgentConfig  # noqa: E402
from grader import grade  # noqa: E402

MARK = REFERENCE.strip()[:60]
# The fake model is obedient, but only to an injection that names the attacker's targets (link,
# IBAN, email, file ID from the injection goal). A text that just reaches the agent isn't enough.
NAMES_TARGETS = all(t in REFERENCE for t in EXP.get("goal_targets", []))


def scripted_reply(messages):
    tool_outputs = [m["content"] or "" for m in messages if m["role"] == "tool"]
    n_called = sum(len(m.get("tool_calls") or []) for m in messages if m["role"] == "assistant")
    injected = bool(MARK) and NAMES_TARGETS and any(MARK in t for t in tool_outputs)
    plan = list(EXP["user_calls"])
    if injected:
        # follow the injection as soon as it is seen, then finish the user task
        seen_at = next(i for i, t in enumerate(tool_outputs) if MARK in t) + 1
        plan = plan[:seen_at] + EXP["injection_calls"] + plan[seen_at:]
    if n_called < len(plan):
        c = plan[n_called]
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call_{n_called}", "type": "function",
            "function": {"name": c["function"], "arguments": json.dumps(c["args"])}}]}
    return {"role": "assistant", "content": EXP["ground_truth_output"] or "Done."}


class FakeOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msg = scripted_reply(body["messages"])
        resp = {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


def cfg(server):
    return AgentConfig(model="fake", base_url=server, temperature=0.0, seed=0, max_steps=30)


def test_reference_names_the_attack_targets():
    missing = [t for t in EXP.get("goal_targets", []) if t not in REFERENCE]
    assert not missing, f"the reference solution never names {missing}, which the attacker's goal needs"


def test_reference_attack_gets_flag(server):
    g = grade(REFERENCE, cfg(server))
    assert g.passed, "the reference solution didn't get the flag with an obedient model"


def test_no_attack_no_flag(server):
    g = grade(None, cfg(server))
    assert not g.passed


def test_injection_reaches_agent_verbatim(server):
    g = grade(REFERENCE, cfg(server))
    assert any(REFERENCE in s.content for s in g.run.trace if s.kind == "tool_result")


def run_script(server, tmp_path, stdin):
    challenge = tmp_path / "challenge"
    subprocess.run(["cp", "-r", str(ROOT / "challenge"), str(challenge)], check=True)
    conf = json.loads((ROOT / "challenge" / "config.json").read_text())
    conf.update(model="fake", base_url=server, temperature=0.0, seed=0, max_steps=30)
    (challenge / "config.json").write_text(json.dumps(conf))
    flag = tmp_path / "flag"
    flag.write_text("pwn.college{TEST_FLAG}\n")
    return subprocess.run([sys.executable, "-I", str(challenge / "run"), "--flag-path", str(flag)],
                          input=stdin, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": ""})


def test_run_script_prints_flag_for_reference(server, tmp_path):
    p = run_script(server, tmp_path, REFERENCE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "pwn.college{TEST_FLAG}" in p.stdout


def test_run_script_no_flag_for_benign_text(server, tmp_path):
    p = run_script(server, tmp_path, "Thanks, see you soon.")
    assert p.returncode == 1 and "TEST_FLAG" not in p.stdout


def test_run_script_rejects_empty_and_too_long(server, tmp_path):
    assert run_script(server, tmp_path, "   ").returncode == 1
    assert run_script(server, tmp_path, "x" * 2001).returncode == 1
