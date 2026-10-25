"""Tool-calling agent loop for 03-agentharm-database-audit.

Same loop as the AgentDojo challenges, with one difference: the user instruction is
passed in (the student writes it), rather than being fixed while the attack rides in
the environment data. Tool calls are executed against a fresh AuditEnvironment and
recorded in the trace, which is what the grader inspects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import challenge_env as be


@dataclass
class AgentConfig:
    model: str
    base_url: str
    temperature: float = 0.7
    seed: Optional[int] = None
    max_steps: int = 15
    timeout_s: float = 300.0
    extra_body: dict = field(default_factory=dict)
    user_task: str = be.DEFAULT_USER_TASK


@dataclass
class Step:
    kind: str          # "tool_call" | "tool_result" | "assistant"
    name: str = ""
    args: Any = None
    content: str = ""
    error: Optional[str] = None


@dataclass
class RunResult:
    final_output: str
    trace: list[Step]
    steps_used: int
    hit_step_limit: bool

    def calls(self) -> list[tuple[str, dict]]:
        return [(s.name, s.args) for s in self.trace if s.kind == "tool_call"]


def make_client(cfg: AgentConfig):
    from openai import OpenAI
    return OpenAI(base_url=cfg.base_url, api_key="ollama", timeout=cfg.timeout_s)


def run_agent(env: be.AuditEnvironment, user_message: str, cfg: AgentConfig, client=None) -> RunResult:
    client = client or make_client(cfg)
    tools = be.openai_tool_specs()
    messages: list[dict] = [
        {"role": "system", "content": be.SYSTEM_MESSAGE},
        {"role": "user", "content": user_message},
    ]
    trace: list[Step] = []
    final = ""

    for step in range(1, cfg.max_steps + 1):
        kwargs = dict(model=cfg.model, messages=messages, tools=tools, temperature=cfg.temperature)
        if cfg.seed is not None:
            kwargs["seed"] = cfg.seed
        if cfg.extra_body:
            kwargs["extra_body"] = cfg.extra_body
        msg = client.chat.completions.create(**kwargs).choices[0].message

        calls = msg.tool_calls or []
        assistant: dict = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                for c in calls
            ]
        messages.append(assistant)
        if msg.content:
            trace.append(Step("assistant", content=msg.content))
        final = msg.content or ""

        if not calls:
            return RunResult(final, trace, step, False)

        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                args, out, err = {}, "", f"ArgumentParseError: {e}"
            else:
                out, err = be.run_tool(env, c.function.name, args)
            trace.append(Step("tool_call", name=c.function.name, args=args))
            trace.append(Step("tool_result", name=c.function.name, content=out, error=err))
            messages.append({"role": "tool", "tool_call_id": c.id, "content": err or out})

    return RunResult(final, trace, cfg.max_steps, True)


def format_trace(result: RunResult, max_result_chars: int = 600) -> str:
    lines = []
    for s in result.trace:
        if s.kind == "tool_call":
            lines.append(f"  -> {s.name}({json.dumps(s.args)})")
        elif s.kind == "tool_result":
            body = s.error or s.content
            body = body if len(body) <= max_result_chars else body[:max_result_chars] + " ...[truncated]"
            lines.append("     " + body.replace("\n", "\n     "))
        else:
            lines.append(f"  [agent] {s.content}")
    if result.hit_step_limit:
        lines.append(f"  (stopped: step limit {result.steps_used} reached)")
    return "\n".join(lines)
