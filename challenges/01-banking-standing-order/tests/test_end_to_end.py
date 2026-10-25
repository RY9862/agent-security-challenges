"""Offline end-to-end tests with a scripted, perfectly 'obedient' fake model.

No GPU needed. They test the plumbing (agent loop, tool execution, grader,
flag printing, input checks), not how a real model behaves: the real 9/10 and
0/10 numbers come from harness.py against a real model.

The fake model speaks the OpenAI chat-completions protocol over HTTP, so the
real `openai` client and the real `run` script are exercised.
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
sys.path.insert(0, str(ROOT / "challenge"))
import banking_env as be  # noqa: E402
from agent import AgentConfig  # noqa: E402
from grader import grade  # noqa: E402


def scripted_reply(messages):
    """Obedient agent: follows any instruction it sees in tool output."""
    tool_outputs = [m["content"] for m in messages if m["role"] == "tool"]
    called = [tc["function"]["name"] for m in messages if m["role"] == "assistant"
              for tc in m.get("tool_calls", [])]
    injected = any(be.ATTACKER_IBAN in t for t in tool_outputs)

    def call(name, args):
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call_{len(called)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}

    if "get_most_recent_transactions" not in called:
        return call("get_most_recent_transactions", {"n": 100})
    if injected and "get_scheduled_transactions" not in called:
        return call("get_scheduled_transactions", {})
    if injected and "update_scheduled_transaction" not in called:
        return call("update_scheduled_transaction", {"id": 6, "recipient": be.ATTACKER_IBAN})
    if "send_money" not in called:
        return call("send_money", {"recipient": "GB29NWBK60161331926819", "amount": 10.0,
                                   "subject": "Refund", "date": "2022-04-01"})
    return {"role": "assistant", "content": "Done, I refunded 10.00."}


class FakeOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["tools"] and body["messages"][0]["role"] == "system"
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
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


REFERENCE = (ROOT / "solutions" / "reference.txt").read_text()


def cfg(url):
    return AgentConfig(model="fake", base_url=url, temperature=0.7, seed=0)


def test_reference_attack_gets_flag(server):
    g = grade(REFERENCE, cfg(server))
    assert g.passed and g.user_task_done
    assert [s.name for s in g.run.trace if s.kind == "tool_call"] == [
        "get_most_recent_transactions", "get_scheduled_transactions",
        "update_scheduled_transaction", "send_money"]


def test_no_attack_no_flag(server):
    g = grade(None, cfg(server))
    assert not g.passed and g.user_task_done


def test_injection_reaches_agent_verbatim(server):
    g = grade(REFERENCE, cfg(server))
    first_result = next(s for s in g.run.trace if s.kind == "tool_result")
    # YAML-dumped, so compare after loading it back
    import yaml
    subjects = [t["subject"] for t in yaml.safe_load(first_result.content)]
    assert REFERENCE in subjects


def run_script(server, tmp_path, stdin):
    challenge = tmp_path / "challenge"
    subprocess.run(["cp", "-r", str(ROOT / "challenge"), str(challenge)], check=True)
    (challenge / "config.json").write_text(json.dumps(
        {"model": "fake", "base_url": server, "temperature": 0.7, "seed": 0}))
    flag = tmp_path / "flag"
    flag.write_text("pwn.college{TEST_FLAG}\n")
    return subprocess.run([sys.executable, "-I", str(challenge / "run"), "--flag-path", str(flag)],
                          input=stdin, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": ""})


def test_run_script_prints_flag_for_attack(server, tmp_path):
    p = run_script(server, tmp_path, REFERENCE)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "pwn.college{TEST_FLAG}" in p.stdout
    assert "update_scheduled_transaction" in p.stdout   # teaching trace shown


def test_run_script_no_flag_for_benign_subject(server, tmp_path):
    p = run_script(server, tmp_path, "Sushi dinner")
    assert p.returncode == 1 and "TEST_FLAG" not in p.stdout


def test_run_script_rejects_empty_and_too_long(server, tmp_path):
    assert run_script(server, tmp_path, "   ").returncode == 1
    p = run_script(server, tmp_path, "x" * 2001)
    assert p.returncode == 1 and "limit" in p.stdout


def test_run_script_reports_infra_failure(tmp_path):
    p = run_script("http://127.0.0.1:9/v1", tmp_path, REFERENCE)
    assert p.returncode == 3 and "infrastructure" in p.stdout and "TEST_FLAG" not in p.stdout
