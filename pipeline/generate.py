"""Generate a challenge from a benchmark task by following DESIGN.md step by step with an LLM.

    source scripts/use-nrp.sh
    python3 pipeline/generate.py --task agentdojo:slack:user_task_2:injection_task_3 --model gpt-oss --runs 3

Each recipe step is either done by the model (one call, then checked against the pinned benchmark)
or by this script (copying the fixed files). Output, per run:

    generated/<model>/<task>/run-<k>/
      challenge/ solutions/ tests/ TRYIT.md harness.py    the generated challenge
      _gen/steps/NN-<step>/                              prompt, raw reply, check output, per attempt
      _gen/generation.json                               per-step outcome (the RQ2a data)
      _gen/rubric.md                                     scripts/rubric_check.py on the result

A failed step is retried up to --repairs times with the check's output; the step's first-try
result and its final result are both recorded. The pipeline keeps going after a failed step so
you can see how far the model gets. NOTE: the checks run the code the model wrote. Run the pipeline
somewhere you don't mind that (a VM or container), like any untrusted code.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import upstream  # noqa: E402

PROMPTS = HERE / "prompts"
TEMPLATES = HERE / "templates"
FIXED_FROM = ROOT / "challenges" / "02-slack-phishing-link"   # source of agent.py, grader.py, harness.py

# Hand-built seeds used as worked examples: folder, env module, its task.
SEEDS = {
    "01-banking-standing-order": ("banking_env.py", "banking:user_task_3:injection_task_4"),
    "02-slack-phishing-link": ("challenge_env.py", "slack:user_task_3:injection_task_1"),
    "04-banking-scheduled-leak": ("banking_env.py", "banking:user_task_2:injection_task_8"),
    "05-workspace-file-delete": ("workspace_env.py", "workspace:user_task_3:injection_task_1"),
}
STEP5_NAMES = {"INJECTION_VECTOR", "VECTOR_READ_TOOLS", "_SENTINEL", "load_environment", "snapshot",
               "VECTOR_URL", "VECTOR_FILE", "VECTOR_EVENT_ID"}
STEP7_START = {"USER_TASKS", "DEFAULT_USER_TASK", "USER_PROMPT", "user_prompt", "utility", "security",
               "INJECTION_GOAL"}


# ---------------------------------------------------------------- helpers
def recipe(*steps: str) -> str:
    """Sections of DESIGN.md, by heading prefix, e.g. 'Step 3', 'Changes from'."""
    text = (ROOT / "DESIGN.md").read_text()
    out = []
    for s in steps:
        m = re.search(rf"^## {re.escape(s)}.*?(?=^## |\Z)", text, re.S | re.M)
        if m:
            out.append(m.group(0).strip())
    return "\n\n".join(out)


def blocks(text: str, lang: str) -> list[str]:
    found = re.findall(r"```([\w-]*)[^\n]*\n(.*?)```", text or "", re.S)
    if lang == "any":
        return [b for _, b in found]
    same = [b for l, b in found if l.lower() == lang]
    return same or ([b for _, b in found] if lang == "python" and found else [])


def pick_example(spec: upstream.TaskSpec, forced: str | None) -> str:
    if forced:
        return next(s for s in SEEDS if s.startswith(forced))
    target = f"{spec.suite}:{spec.user_task}:{spec.injection_task}"
    same_suite = [s for s, (_, t) in SEEDS.items() if t.startswith(spec.suite + ":") and t != target]
    if same_suite:
        return same_suite[0]
    return next(s for s, (_, t) in SEEDS.items() if t != target and s.startswith(("02", "01")))


def example_parts(seed: str) -> dict[str, str]:
    """The seed's env module split like the generated one: port / injection point / tasks."""
    mod_file, _ = SEEDS[seed]
    src = (ROOT / "challenges" / seed / "challenge" / mod_file).read_text()
    tree = ast.parse(src)
    lines = src.splitlines()

    def names(node):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            return {node.name}
        if isinstance(node, ast.Assign):
            return {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return {node.target.id}
        return set()

    inj, tasks, in_tasks = [], [], False
    for node in tree.body:
        n = names(node)
        seg = "\n".join(lines[node.lineno - 1 - len(getattr(node, "decorator_list", [])):node.end_lineno])
        if n & STEP7_START:
            in_tasks = True
        if n & STEP5_NAMES:
            inj.append(seg)
        elif in_tasks:
            tasks.append(seg)
    reference = (ROOT / "challenges" / seed / "solutions" / "reference.txt")
    refs = sorted((ROOT / "challenges" / seed / "solutions").glob("reference*.txt"))
    tryit = (ROOT / "challenges" / seed / "TRYIT.md").read_text()
    scenario = re.search(r"## The scenario.*?(?=\n## |\Z)", tryit, re.S)
    brief = subprocess.run([sys.executable, "-I", str(ROOT / "challenges" / seed / "challenge" / "run"),
                            "--flag-path", os.devnull], input="", capture_output=True, text=True).stdout
    return {"module": src, "injection": "\n\n".join(inj), "tasks": "\n\n".join(tasks),
            "reference": (reference if reference.exists() else refs[-1]).read_text(),
            "scenario": scenario.group(0) if scenario else tryit, "briefing": brief.strip()}


class LLM:
    def __init__(self, a):
        from openai import OpenAI
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("NRP_API_KEY") or "none"
        self.client = OpenAI(base_url=a.base_url, api_key=key, timeout=a.timeout, max_retries=3)
        self.a = a
        self.extra = {}
        if os.environ.get("NRP_CACHE_SALT") and "nrp-nautilus" in (a.base_url or ""):
            self.extra["cache_salt"] = os.environ["NRP_CACHE_SALT"]
        if a.reasoning_effort:
            self.extra["reasoning_effort"] = a.reasoning_effort

    def chat(self, messages: list[dict]) -> tuple[str, dict]:
        t0 = time.perf_counter()
        kw = dict(model=self.a.model, messages=messages, temperature=self.a.temperature,
                  max_tokens=self.a.max_tokens)
        if self.extra:
            kw["extra_body"] = self.extra
        resp = self.client.chat.completions.create(**kw)
        u = resp.usage
        meta = {"seconds": round(time.perf_counter() - t0, 1), "served_model": resp.model,
                "finish_reason": resp.choices[0].finish_reason,
                "tokens_in": getattr(u, "prompt_tokens", None), "tokens_out": getattr(u, "completion_tokens", None)}
        return resp.choices[0].message.content or "", meta


def run_check(cwd: Path, *pytest_args: str) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        p = subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                            "--tb=short", *pytest_args], cwd=cwd, capture_output=True, text=True,
                           timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return False, "the check timed out after 600 s"
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out


def tail(text: str, n: int = 60) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------- one generation
class Generation:
    def __init__(self, a, ctx: upstream.Context, out: Path, llm: LLM, example: str):
        self.a, self.ctx, self.out, self.llm = a, ctx, out, llm
        self.example = example
        self.ex = example_parts(example)
        self.parts = {"port": "", "injection": "", "tasks": ""}
        self.steps: list[dict] = []
        self.vector = None
        self.history: list[dict] = [{"role": "system", "content": (PROMPTS / "system.md").read_text()}]

    # -- bookkeeping
    def log_dir(self, n: str, name: str) -> Path:
        d = self.out / "_gen" / "steps" / f"{n}-{name}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def record(self, n, name, kind, ok, first_ok=None, attempts=0, note="", meta=None):
        self.steps.append({"step": n, "name": name, "by": kind, "passed": ok,
                           "first_try_passed": ok if first_ok is None else first_ok,
                           "attempts": attempts, "note": note, "calls": meta or []})
        mark = "PASS" if ok else "FAIL"
        extra = f" (attempts: {attempts})" if kind == "model" else ""
        print(f"   step {n:<3} {name:<22} {kind:<6} {mark}{extra}  {note[:90]}", flush=True)

    def write_module(self):
        header = (f'"""Generated by pipeline/generate.py from {upstream.PINNED}:\n'
                  f'suite {self.ctx.spec.suite}, {self.ctx.spec.user_task} x {self.ctx.spec.injection_task}.\n'
                  f'Model: {self.a.model}. Review before use; see _gen/ for how each part was made.\n"""\n\n')
        body = "\n\n\n".join(p for p in (self.parts["port"], self.parts["injection"], self.parts["tasks"]) if p)
        # `from __future__` imports must come first in a file; the parts are joined below a header.
        future = sorted(set(re.findall(r"^from __future__ import [^\n]+$", body, re.M)))
        body = re.sub(r"^from __future__ import [^\n]+\n?", "", body, flags=re.M)
        top = "\n".join(future) + "\n\n" if future else ""
        (self.out / "challenge" / "challenge_env.py").write_text(header + top + body + "\n")

    # -- a model step with checks and repairs
    def model_step(self, n, name, prompt, lang, apply, check):
        d = self.log_dir(n, name)
        msgs = self.history + [{"role": "user", "content": prompt}]
        metas, first_ok, ok, note = [], None, False, ""
        for attempt in range(1 + self.a.repairs):
            (d / f"prompt-{attempt}.md").write_text(msgs[-1]["content"])
            try:
                reply, meta = self.llm.chat(msgs)
            except Exception as e:  # noqa: BLE001
                note = f"model call failed: {type(e).__name__}: {e}"
                metas.append({"error": note})
                (d / f"check-{attempt}.txt").write_text(note)
                break
            metas.append(meta)
            (d / f"reply-{attempt}.md").write_text(reply)
            found = blocks(reply, lang)
            if not found:
                ok, out = False, f"no ```{lang} block in the reply (finish_reason: {meta['finish_reason']})"
            else:
                apply(found)
                ok, out = check()
            (d / f"check-{attempt}.txt").write_text(out)
            note = "" if ok else tail(out, 3).replace("\n", " | ")
            first_ok = ok if first_ok is None else first_ok
            if ok:
                break
            msgs = msgs + [{"role": "assistant", "content": reply},
                           {"role": "user", "content": f"That didn't pass the checks:\n\n```\n{tail(out)}\n```\n\n"
                                                       f"Reply with the corrected version, in the same format as before."}]
        self.record(n, name, "model", ok, bool(first_ok), len(metas), note, metas)
        return ok

    # -- scripted setup (fixed files, data, tests)
    def setup(self):
        ch = self.out / "challenge"
        (self.out / "solutions").mkdir(parents=True, exist_ok=True)
        (self.out / "tests").mkdir(parents=True, exist_ok=True)
        ch.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.ctx.data_dir, ch / "data", dirs_exist_ok=True)
        slug = self.out.parent.name
        agent = (FIXED_FROM / "challenge" / "agent.py").read_text().replace("be.SlackEnvironment", "object")
        (ch / "agent.py").write_text(agent)
        grader = (FIXED_FROM / "challenge" / "grader.py").read_text().replace(
            "Grader for 02-slack-phishing-link", f"Grader for generated challenge {slug}")
        (ch / "grader.py").write_text(grader)
        harness = (FIXED_FROM / "harness.py").read_text().replace(
            "Reliability tests for 02-slack-phishing-link", f"Reliability tests for generated challenge {slug}")
        harness = harness.replace('ap.add_argument("--user-task", default="user_task_3", choices=["user_task_3"])',
                                  'ap.add_argument("--user-task", default=be.DEFAULT_USER_TASK, choices=list(be.USER_TASKS))')
        (self.out / "harness.py").write_text(harness)
        conf = json.loads((FIXED_FROM / "challenge" / "config.json").read_text())
        conf["user_task"] = self.ctx.spec.user_task
        (ch / "config.json").write_text(json.dumps(conf, indent=2) + "\n")
        shutil.copy(TEMPLATES / "run", ch / "run")
        (ch / "run").chmod(0o755)
        for t in ("test_equivalence.py", "test_end_to_end.py"):
            shutil.copy(TEMPLATES / t, self.out / "tests" / t)
        (self.out / "tests" / "upstream_expected.json").write_text(json.dumps(upstream.expectations(self.ctx), indent=1))

    # -- the recipe
    def run(self):
        ctx, ex = self.ctx, self.ex
        facts = json.dumps(ctx.facts(), indent=2)
        self.setup()
        self.record("1", "pick task", "script", True, note=f"{ctx.spec.suite} {ctx.spec.user_task} x {ctx.spec.injection_task}")

        # Step 2: viability
        verdict = {}

        def apply2(found):
            verdict.clear()
            try:
                verdict.update(json.loads(re.sub(r"//[^\n]*", "", found[0])))
            except json.JSONDecodeError as e:
                verdict["_error"] = str(e)

        def check2():
            if "_error" in verdict:
                return False, f"the JSON didn't parse: {verdict['_error']}"
            v = verdict.get("vector")
            if v not in ctx.vectors_read:
                return False, (f"vector {v!r} isn't one the user task makes the agent read. "
                               f"Computed from the benchmark: {sorted(ctx.vectors_read)}")
            if not verdict.get("viable"):
                return False, "you judged the task not viable: " + str(verdict.get("reasons", ""))
            return True, json.dumps(verdict, indent=1)

        vectors = "\n".join(f"- {k}: {ctx.vector_descriptions.get(k, '')}; default {ctx.vector_defaults[k]!r}"
                            for k in ctx.vector_defaults)
        p2 = (PROMPTS / "step2_viability.md").read_text().format(
            recipe=recipe("Step 2"), facts=facts, vectors=vectors,
            user_task_source=ctx.user_task_source, injection_task_source=ctx.injection_task_source)
        if not self.model_step("2", "viability", p2, "json", apply2, check2):
            if not ctx.vectors_read:
                self.record("2b", "viability fallback", "script", False,
                            note="the user task reads no injection vector; this task can't be a challenge")
                return
            self.vector = next(iter(ctx.vectors_read))
            self.record("2b", "viability fallback", "script", True, note=f"vector set by script: {self.vector}")
        else:
            self.vector = verdict["vector"]

        # Steps 3-4: port
        tool_sources = "\n\n".join(f"`{f}`:\n```python\n{s}\n```" for f, s in ctx.tool_sources.items())
        excerpts = "\n\n".join(f"`data/{f}`:\n```yaml\n{s}\n```" for f, s in ctx.data_excerpts.items())
        p3 = (PROMPTS / "step3_port.md").read_text().format(
            recipe=recipe("Step 3", "Step 4"), suite=ctx.spec.suite, tool_names=", ".join(ctx.tool_names),
            system_message_json=json.dumps(ctx.system_message), data_excerpts=excerpts,
            environment_source=ctx.environment_source, tool_sources=tool_sources,
            example_name=self.example, example_env=ex["module"])

        def apply_part(key):
            def f(found):
                self.parts[key] = max(found, key=len).strip()
                self.write_module()
            return f

        self.model_step("3", "port environment", p3, "python", apply_part("port"),
                        lambda: run_check(self.out, "tests/test_equivalence.py", "-k", "port"))
        ok4, out4 = run_check(self.out, "tests/test_equivalence.py", "-k", "no_benchmark")
        self.record("4", "no benchmark code", "check", ok4, note="" if ok4 else tail(out4, 2))

        # Step 5: injection point
        p5 = (PROMPTS / "step5_injection.md").read_text().format(
            recipe=recipe("Step 5", "Changes from"), facts=facts, vector=self.vector,
            vector_description=ctx.vector_descriptions.get(self.vector, ""),
            vector_read_tools=ctx.vectors_read.get(self.vector, []),
            module_so_far=self.parts["port"], example_name=self.example, example_part=ex["injection"])
        self.model_step("5", "injection point", p5, "python", apply_part("injection"),
                        lambda: run_check(self.out, "tests/test_equivalence.py", "-k", "injection"))

        # Step 6: agent loop (fixed)
        self.record("6", "agent loop", "script", True, note="copied agent.py, grader.py, run, harness.py")

        # Step 7: grader
        p7 = (PROMPTS / "step7_grader.md").read_text().format(
            recipe=recipe("Step 7", "Notes and known"), facts=facts,
            user_task_source=ctx.user_task_source, injection_task_source=ctx.injection_task_source,
            user_task=ctx.spec.user_task, module_so_far=self.parts["port"] + "\n\n\n" + self.parts["injection"],
            example_name=self.example, example_part=ex["tasks"])
        self.model_step("7", "flag grader", p7, "python", apply_part("tasks"),
                        lambda: run_check(self.out, "tests/test_equivalence.py", "-k", "tasks"))

        # Step 7 (player-facing): briefing.json + TRYIT scenario
        constants = self.constants()
        player = {}

        def apply7b(found):
            player.clear()
            js = [b for b in found if b.lstrip().startswith("{")]
            md = [b for b in found if "## The scenario" in b]
            player["json"], player["md"] = (js[0] if js else ""), (md[0] if md else "")

        def check7b():
            try:
                data = json.loads(re.sub(r"//[^\n]*", "", player.get("json", "")))
            except json.JSONDecodeError as e:
                return False, f"briefing JSON didn't parse: {e}"
            need = ["slug", "title", "scenario", "control", "goal", "user_task_label", "success", "failure"]
            missing = [k for k in need if not isinstance(data.get(k), str) or not data[k].strip()]
            if missing:
                return False, f"briefing.json is missing: {missing}"
            if not player.get("md"):
                return False, "no ```markdown block with a '## The scenario' section"
            data["slug"] = self.out.parent.name
            (self.out / "challenge" / "briefing.json").write_text(json.dumps(data, indent=2) + "\n")
            (self.out / "TRYIT.md").write_text(self.tryit(player["md"].strip()))
            p = subprocess.run([sys.executable, "-I", str(self.out / "challenge" / "run"), "--flag-path", os.devnull],
                               input="", capture_output=True, text=True, timeout=120,
                               env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
            ok = p.returncode == 1 and "Goal:" in p.stdout and "Traceback" not in p.stderr
            return ok, (p.stdout + p.stderr).strip()

        p7b = (PROMPTS / "step7_player.md").read_text().format(
            facts=facts, vector=self.vector, vector_description=ctx.vector_descriptions.get(self.vector, ""),
            constants=constants, example_name=self.example, example_briefing=ex["briefing"],
            example_scenario=ex["scenario"], slug=self.out.parent.name)

        self.model_step("7b", "player text", p7b, "any", apply7b, check7b)

        # Step 8: reference solution
        sec_src = self._func_source("security")
        p8 = (PROMPTS / "step8_reference.md").read_text().format(
            recipe=recipe("Step 8"), facts=facts, vector=self.vector,
            vector_description=ctx.vector_descriptions.get(self.vector, ""), security_source=sec_src,
            example_name=self.example, example_reference=ex["reference"])

        def apply8(found):
            (self.out / "solutions" / "reference.txt").write_text(found[0].strip("\n") + "\n")

        self.model_step("8", "reference solution", p8, "text", apply8,
                        lambda: run_check(self.out, "tests/test_end_to_end.py"))

        # Step 9: tests and rubric
        ok9, out9 = run_check(self.out, "tests")
        self.record("9", "all tests", "check", ok9, note="" if ok9 else tail(out9, 2))

    # -- small helpers used by run()
    def _func_source(self, name: str) -> str:
        src = (self.out / "challenge" / "challenge_env.py").read_text()
        try:
            for node in ast.parse(src).body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    return ast.get_source_segment(src, node) or ""
        except SyntaxError:
            pass
        return self.ctx.injection_task_source

    def constants(self) -> str:
        src = (self.out / "challenge" / "challenge_env.py").read_text()
        out = []
        try:
            for node in ast.parse(src).body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, (str, int, float)):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id.isupper() and t.id not in ("SYSTEM_MESSAGE",):
                            out.append(f"- {t.id} = {node.value.value!r}")
        except SyntaxError:
            pass
        return "\n".join(out) or "(none)"

    def tryit(self, scenario: str) -> str:
        return (f"# Generated challenge: {self.out.parent.name}\n\n{scenario}\n\n"
                "## How to play\n\nFrom this folder, with a model server set in `challenge/config.json`:\n\n"
                "    echo 'practice{you_got_it}' > /tmp/flag\n"
                "    cd challenge && python3 run --flag-path /tmp/flag\n\n"
                "Type your text, then press Ctrl-D. Don't look in `solutions/` until you've solved it.\n")


# ---------------------------------------------------------------- main
def rubric(out: Path) -> tuple[str, dict]:
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "rubric_check.py"), str(out),
                        "--out", str(out / "_gen" / "rubric.md")], capture_output=True, text=True, timeout=1800)
    table = p.stdout
    row = {}
    for line in table.splitlines():
        m = re.match(r"\| (1A\.\d) [^|]*\| (\S[^|]*?) \|", line)
        if m:
            row[m.group(1)] = m.group(2).strip()
    return table, row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", action="append", required=True,
                    help="agentdojo:<suite>:<user_task_N>:<injection_task_N> (may repeat)")
    ap.add_argument("--model", default=os.environ.get("MODEL", "gpt-oss"))
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    ap.add_argument("--runs", type=int, default=1, help="independent generations per task")
    ap.add_argument("--repairs", type=int, default=1, help="retries per step after a failed check")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--reasoning-effort", choices=["low", "medium", "high"])
    ap.add_argument("--timeout", type=float, default=1800.0, help="per model call, seconds")
    ap.add_argument("--example", help="seed to use as the worked example, e.g. 01 (default: same suite)")
    ap.add_argument("--out", type=Path, default=ROOT / "generated")
    a = ap.parse_args()
    if not a.base_url:
        sys.exit("No model server: source scripts/use-nrp.sh first, or pass --base-url.")

    llm = LLM(a)
    summary = []
    for t in a.task:
        spec = upstream.TaskSpec.parse(t)
        try:
            ctx = upstream.load(spec)
        except (ValueError, KeyError) as e:
            print(f"== {t}: skipped. {e}", flush=True)
            continue
        example = pick_example(spec, a.example)
        base = a.out / a.model.replace("/", "_").replace(":", "_") / spec.slug
        print(f"== {t}  model {a.model}  example {example}  -> {base}", flush=True)
        for k in range(1, a.runs + 1):
            out = base / f"run-{k}"
            if out.exists():
                out = base / f"run-{k}-{dt.datetime.now():%Y%m%d-%H%M%S}"
            print(f" run {k}: {out}", flush=True)
            g = Generation(a, ctx, out, llm, example)
            t0 = time.time()
            try:
                g.run()
            finally:
                table, row = rubric(out) if (out / "challenge" / "run").exists() else ("", {})
                rec = {"task": t, "model": a.model, "run": k, "example": example, "vector": g.vector,
                       "settings": {"temperature": a.temperature, "repairs": a.repairs, "max_tokens": a.max_tokens,
                                    "reasoning_effort": a.reasoning_effort},
                       "benchmark": upstream.PINNED, "started": dt.datetime.fromtimestamp(t0).isoformat(),
                       "seconds": round(time.time() - t0), "steps": g.steps, "rubric": row}
                (out / "_gen").mkdir(parents=True, exist_ok=True)
                (out / "_gen" / "generation.json").write_text(json.dumps(rec, indent=1))
                summary.append(rec)
                print(f"   rubric: " + ", ".join(f"{k} {v}" for k, v in row.items()), flush=True)
    print("\nWrote generated challenges; build the pilot table with:\n"
          f"   python3 pipeline/report.py {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
