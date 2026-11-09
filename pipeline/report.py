"""Pilot table for generated challenges (reads every generated/**/_gen/generation.json).

    python3 pipeline/report.py generated/            # writes generated/report.md and generated/report.tsv

Two tables:
  1. One row per generation: each recipe step's outcome and the scripted rubric checks.
     Steps: ok = passed first try, ok(N) = passed after N attempts, FAIL = still failing,
     - = done by the script.
  2. Per recipe step across all generations (RQ2a): how often the model got it right first try,
     and after repairs.
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

STEPS = [("2", "viability"), ("3", "port"), ("4", "no bench code"), ("5", "injection"),
         ("7", "grader"), ("7b", "player text"), ("8", "reference"), ("9", "all tests")]
RUBRIC = ["1A.1", "1A.2", "1A.3", "1A.4", "1A.5", "1A.8"]


def cell(s: dict | None) -> str:
    if s is None:
        return "not reached"
    if s["by"] != "model":
        return "ok" if s["passed"] else "FAIL"
    if s["passed"]:
        return "ok" if s["first_try_passed"] else f"ok({s['attempts']})"
    return "FAIL"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "generated")
    recs = [json.loads(p.read_text()) for p in sorted(root.rglob("_gen/generation.json"))]
    if not recs:
        sys.exit(f"no generations under {root}")

    head = ["Task", "Model", "Run"] + [f"{n} {name}" for n, name in STEPS] + RUBRIC + ["Minutes", "Tokens out"]
    rows, per_step = [], defaultdict(lambda: {"n": 0, "first": 0, "final": 0})
    for r in recs:
        by = {s["step"]: s for s in r["steps"]}
        if "2b" in by and by["2"]["passed"] is False:
            by["2"] = {**by["2"], "note": by["2b"]["note"]}
        tokens = sum((c.get("tokens_out") or 0) for s in r["steps"] for c in s.get("calls", []))
        rows.append([r["task"].replace("agentdojo:", ""), r["model"], r["run"]]
                    + [cell(by.get(n)) for n, _ in STEPS]
                    + [r.get("rubric", {}).get(k, "-") for k in RUBRIC]
                    + [round(r["seconds"] / 60, 1), tokens])
        for n, name in STEPS:
            s = by.get(n)
            if s and s["by"] == "model":
                per_step[name]["n"] += 1
                per_step[name]["first"] += bool(s["first_try_passed"])
                per_step[name]["final"] += bool(s["passed"])

    def md(h, rs):
        return "\n".join(["| " + " | ".join(map(str, h)) + " |", "|" + "---|" * len(h)]
                         + ["| " + " | ".join(map(str, x)) + " |" for x in rs])

    step_rows = [[name, v["n"], f"{v['first']}/{v['n']}", f"{v['final']}/{v['n']}"] for name, v in per_step.items()]
    report = (f"# Generation pilot\n\n{len(recs)} generations under `{root}`.\n\n"
              "## Each generation\n\nok = first try, ok(N) = after N attempts, FAIL = still failing after repairs. "
              "Rubric columns are `scripts/rubric_check.py` (offline checks).\n\n"
              + md(head, rows) + "\n\n## Per recipe step (RQ2a)\n\n"
              + md(["Step (by the model)", "Generations", "Right first try", "Right after repairs"], step_rows) + "\n")
    print(report)
    (root / "report.md").write_text(report)
    with (root / "report.tsv").open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(head)
        w.writerows(rows)
    print(f"Saved: {root / 'report.md'}\n       {root / 'report.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
