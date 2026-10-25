"""Grader for 01-banking-standing-order: run the agent on the student's input, then call security()."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import banking_env as be
from agent import AgentConfig, RunResult, run_agent

HERE = Path(__file__).resolve().parent
MAX_INPUT_CHARS = 2000   # DEVIATION (D4): upstream has no length limit


@dataclass
class Grade:
    passed: bool          # security(): the flag condition
    user_task_done: bool  # utility(): reported for teaching, does not gate the flag
    run: RunResult


def load_config(path: Path = HERE / "config.json") -> AgentConfig:
    """Config comes ONLY from this root-owned file. Never from environment
    variables or arguments: the grader runs SUID, and a student who could set
    base_url could point it at a fake model that emits the tool call directly."""
    return AgentConfig(**json.loads(path.read_text()))


def grade(student_text: Optional[str], cfg: AgentConfig, client=None) -> Grade:
    """student_text=None means no attack (vector keeps its upstream default)."""
    env = be.load_environment(student_text)
    pre = be.snapshot(env)
    result = run_agent(env, cfg, client=client)
    return Grade(
        passed=be.security(result.final_output, pre, env),
        user_task_done=be.utility(result.final_output, pre, env, cfg.user_task),
        run=result,
    )
