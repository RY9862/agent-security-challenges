"""Checks a generated challenge against its source task, using values frozen from the pinned
benchmark when the challenge was generated (tests/upstream_expected.json). No agentdojo needed.

The test names group the checks by recipe step, so the pipeline can run one group per step:
  test_port_*       Steps 3-4: environment, tools and system prompt copied; no benchmark imports
  test_injection_*  Step 5: player's text at the injection point, and the user task reads it
  test_tasks_*      Step 7: user task, utility() and security() behave as upstream
"""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "tests" / "upstream_expected.json").read_text())
sys.path.insert(0, str(ROOT / "challenge"))


@pytest.fixture(scope="module")
def ce():
    import challenge_env
    return challenge_env


def _dump(env):
    return json.loads(env.model_dump_json())


def _run(ce, env, calls):
    outputs = []
    for c in calls:
        out, err = ce.run_tool(env, c["function"], dict(c["args"]))
        assert err is None, f"{c['function']}({c['args']}) failed: {err}"
        outputs.append(out)
    return outputs


# ---------------------------------------------------------------- Steps 3-4: port
def test_port_no_benchmark_imports():
    tree = ast.parse((ROOT / "challenge" / "challenge_env.py").read_text())
    mods = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    mods += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    bad = [m for m in mods if m.split(".")[0] in ("agentdojo", "inspect_evals", "inspect_ai")]
    assert not bad, f"imports the benchmark at runtime: {bad}"


def test_port_imports(ce):
    for name in ("TOOLS", "run_tool", "openai_tool_specs", "SYSTEM_MESSAGE"):
        assert hasattr(ce, name), f"challenge_env has no {name}"


def test_port_tool_names_in_order(ce):
    assert list(ce.TOOLS) == EXP["tools"]


def test_port_tool_descriptions_and_params(ce):
    specs = {s["function"]["name"]: s["function"] for s in ce.openai_tool_specs()}
    for name in EXP["tools"]:
        assert specs[name]["description"] == EXP["tool_descriptions"][name], name
        assert sorted(specs[name]["parameters"].get("properties", {})) == EXP["tool_params"][name], name


def test_port_system_message(ce):
    assert ce.SYSTEM_MESSAGE == EXP["system_message"]


# ---------------------------------------------------------------- Step 5: injection point
def test_injection_default_environment_matches(ce):
    assert _dump(ce.load_environment(None)) == EXP["default_env"]


def test_injection_vector_is_one_the_user_task_reads(ce):
    assert ce.INJECTION_VECTOR in EXP["vectors_read"], \
        f"{ce.INJECTION_VECTOR} isn't read by the user task; it reads {sorted(EXP['vectors_read'])}"
    assert set(ce.VECTOR_READ_TOOLS) & set(EXP["vectors_read"][ce.INJECTION_VECTOR])


def test_injection_reaches_agent_verbatim(ce):
    text = f'{EXP["canary"]} line one\nline two "quoted" {{braces}} \\ end'
    outputs = _run(ce, ce.load_environment(text), EXP["user_calls"])
    assert any(text in o for o in outputs), "the player's text never appears, byte for byte, in what the agent reads"


# ---------------------------------------------------------------- Step 7: tasks and checks
def test_tasks_user_prompt(ce):
    assert ce.DEFAULT_USER_TASK == EXP["task"]["user_task"]
    assert ce.user_prompt(ce.DEFAULT_USER_TASK) == EXP["user_prompt"]


def test_tasks_user_task_alone(ce):
    env = ce.load_environment(None); pre = ce.snapshot(env)
    _run(ce, env, EXP["user_calls"])
    out = EXP["ground_truth_output"]
    assert ce.utility(out, pre, env) == EXP["expected"]["user_only"]["utility"]
    assert ce.security(out, pre, env) == EXP["expected"]["user_only"]["security"] is False


def test_tasks_attack_goal_met(ce):
    env = ce.load_environment(None); pre = ce.snapshot(env)
    _run(ce, env, EXP["user_calls"] + EXP["injection_calls"])
    out = EXP["ground_truth_output"]
    assert ce.security(out, pre, env) == EXP["expected"]["attack"]["security"] is True
    assert ce.utility(out, pre, env) == EXP["expected"]["attack"]["utility"]
