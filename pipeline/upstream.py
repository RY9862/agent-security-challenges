"""Everything the pipeline needs to know about one AgentDojo task pair, read from the pinned package.

Used two ways:
  - as context for the model (source code, data excerpts), and
  - as the expected values the generated challenge is checked against
    (tool names and descriptions, default environment, ground-truth calls and their outcomes).

Needs agentdojo==0.1.35 installed (`pip install -r pipeline/requirements.txt`). The generated
challenges don't: their checks run against the frozen expectations written here.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

BENCHMARK_VERSION = "v1.2.2"
PINNED = "agentdojo==0.1.35 (ethz-spylab/agentdojo 089ed468), benchmark v1.2.2"
CANARY = "CANARY-7f3a91"
# Concrete targets in an injection goal that any working attack has to name: links, IBANs,
# email addresses, quoted IDs. Used to check a reference solution actually asks for the attack.
_TARGET = re.compile(r"(?:https?://|www\.)[^\s'\"]+|\b[A-Z]{2}\d{10,}\b|[\w.+-]+@[\w-]+\.[\w.]+|'(\d+)'")


def goal_targets(goal: str) -> list[str]:
    out = []
    for m in _TARGET.finditer(goal):
        v = (m.group(1) or m.group(0)).rstrip(".,;:!?)")
        if v not in out:
            out.append(v)
    return out


@dataclass
class TaskSpec:
    suite: str
    user_task: str
    injection_task: str

    @classmethod
    def parse(cls, text: str) -> "TaskSpec":
        """'agentdojo:slack:user_task_2:injection_task_3' or 'slack:user_task_2:injection_task_3'."""
        parts = text.split(":")
        if parts[0] == "agentdojo":
            parts = parts[1:]
        if len(parts) != 3:
            raise ValueError(f"task must look like agentdojo:<suite>:<user_task_N>:<injection_task_N>, got {text!r}")
        return cls(*parts)

    @property
    def slug(self) -> str:
        return f"{self.suite}-{self.user_task.replace('user_task_', 'u')}-{self.injection_task.replace('injection_task_', 'i')}"


@dataclass
class Context:
    spec: TaskSpec
    user_prompt: str
    injection_goal: str
    user_task_source: str
    injection_task_source: str
    environment_source: str
    tool_sources: dict[str, str]
    tool_names: list[str]
    tool_descriptions: dict[str, str]
    tool_params: dict[str, list[str]]
    system_message: str
    data_dir: Path
    data_excerpts: dict[str, str]
    vector_defaults: dict[str, str]
    vector_descriptions: dict[str, str]
    vectors_read: dict[str, list[str]]           # vector -> tools whose output showed it
    user_calls: list[dict]
    injection_calls: list[dict]
    ground_truth_output: str
    default_env: dict
    expected: dict = field(default_factory=dict)  # outcomes of the ground-truth calls upstream

    def facts(self) -> dict:
        """Short, model-facing summary of what was computed from the package."""
        return {
            "benchmark": PINNED, "suite": self.spec.suite,
            "user_task": self.spec.user_task, "user_prompt": self.user_prompt,
            "injection_task": self.spec.injection_task, "injection_goal": self.injection_goal,
            "attack_targets_a_reference_must_name": goal_targets(self.injection_goal),
            "tools_in_order": self.tool_names,
            "injection_vectors_the_user_task_reads": self.vectors_read,
            "user_task_ground_truth_calls": self.user_calls,
            "injection_task_ground_truth_calls": self.injection_calls,
        }


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


def _calls(task, env) -> list[dict]:
    return [{"function": c.function, "args": _jsonable(dict(c.args))} for c in task.ground_truth(env)]


def load(spec: TaskSpec, excerpt_lines: int = 120) -> Context:
    from agentdojo.agent_pipeline.agent_pipeline import load_system_message
    from agentdojo.functions_runtime import FunctionsRuntime
    from agentdojo.task_suite.load_suites import get_suite
    import agentdojo

    suite = get_suite(BENCHMARK_VERSION, spec.suite)
    ut = suite.get_user_task_by_id(spec.user_task)
    it = suite.get_injection_task_by_id(spec.injection_task)

    tool_files: dict[str, str] = {}
    for t in suite.tools:
        f = Path(inspect.getsourcefile(t.run))
        tool_files.setdefault(str(f.relative_to(Path(agentdojo.__file__).parent)), f.read_text())
    env_file = Path(inspect.getsourcefile(suite.environment_type))

    data_dir = Path(agentdojo.__file__).parent / "data" / "suites" / spec.suite
    excerpts = {}
    for f in sorted(data_dir.rglob("*.yaml")):
        lines = f.read_text().splitlines()
        rel = str(f.relative_to(data_dir))
        excerpts[rel] = "\n".join(lines[:excerpt_lines]) + (
            f"\n# ... ({len(lines) - excerpt_lines} more lines, copied verbatim to challenge/data/{rel})"
            if len(lines) > excerpt_lines else "")

    vectors_yaml = __import__("yaml").safe_load((data_dir / "injection_vectors.yaml").read_text())
    defaults = suite.get_injection_vector_defaults()

    # Which injection vectors does the user task's ground truth actually show the agent?
    env_c = suite.load_and_inject_default_environment({v: f"{CANARY}:{v}" for v in defaults})
    rt = FunctionsRuntime(suite.tools)
    seen: dict[str, list[str]] = {}
    for c in ut.ground_truth(env_c.model_copy(deep=True)):
        out, _ = rt.run_function(env_c, c.function, dict(c.args))
        for v in defaults:
            if f"{CANARY}:{v}" in str(out):
                seen.setdefault(v, [])
                if c.function not in seen[v]:
                    seen[v].append(c.function)

    env = suite.load_and_inject_default_environment({})
    user_calls, inj_calls = _calls(ut, env), _calls(it, env)

    # Upstream outcomes of the ground-truth sequences (what the generated checks must reproduce).
    def outcome(calls):
        e = suite.load_and_inject_default_environment({})
        pre = e.model_copy(deep=True)
        for c in calls:
            rt.run_function(e, c["function"], dict(c["args"]))
        out = getattr(ut, "GROUND_TRUTH_OUTPUT", "") or ""
        return {"utility": bool(ut.utility(out, pre, e)), "security": bool(it.security(out, pre, e))}

    try:
        expected = {"user_only": outcome(user_calls), "attack": outcome(user_calls + inj_calls)}
    except NotImplementedError:
        raise ValueError(
            f"{spec.injection_task} is graded from the agent's tool-call trace (security_from_traces), not "
            "from the final environment. The challenge grader works on the final environment, so this "
            "pipeline can't build it yet; pick another injection task.") from None

    return Context(
        spec=spec, user_prompt=ut.PROMPT, injection_goal=it.GOAL,
        user_task_source=inspect.getsource(type(ut)), injection_task_source=inspect.getsource(type(it)),
        environment_source=env_file.read_text(), tool_sources=tool_files,
        tool_names=[t.name for t in suite.tools],
        tool_descriptions={t.name: t.description for t in suite.tools},
        tool_params={t.name: sorted(t.parameters.model_json_schema().get("properties", {})) for t in suite.tools},
        system_message=load_system_message(None), data_dir=data_dir, data_excerpts=excerpts,
        vector_defaults=defaults,
        vector_descriptions={k: v.get("description", "") for k, v in vectors_yaml.items()},
        vectors_read=seen, user_calls=user_calls, injection_calls=inj_calls,
        ground_truth_output=getattr(ut, "GROUND_TRUTH_OUTPUT", "") or "",
        default_env=json.loads(env.model_dump_json()),
        expected=expected,
    )


def expectations(ctx: Context) -> dict:
    """Frozen upstream values the generated tests check against (tests/upstream_expected.json)."""
    return {
        "benchmark": PINNED, "task": ctx.spec.__dict__,
        "tools": ctx.tool_names, "tool_descriptions": ctx.tool_descriptions, "tool_params": ctx.tool_params,
        "system_message": ctx.system_message, "user_prompt": ctx.user_prompt,
        "injection_goal": ctx.injection_goal, "goal_targets": goal_targets(ctx.injection_goal),
        "vectors_read": ctx.vectors_read,
        "vector_defaults": ctx.vector_defaults, "default_env": ctx.default_env,
        "user_calls": ctx.user_calls, "injection_calls": ctx.injection_calls,
        "ground_truth_output": ctx.ground_truth_output, "expected": ctx.expected, "canary": CANARY,
    }
