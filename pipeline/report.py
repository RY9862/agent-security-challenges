"""Pilot table for generated challenges (reads every generated/**/_gen/generation.json).

    python3 pipeline/report.py generated/            # writes generated/report.md and generated/report.tsv

Two tables:
  1. One row per generation: each recipe step's outcome and the scripted rubric checks.
     Steps: ok = passed first try, ok(N) = passed after N attempts, FAIL = still failing,
     - = done by the script.
  2. Per recipe step across all generations (RQ2a): how often the model got it right first try,
     and after repairs.
  3. Working challenges per model and task (RQ1): every test passes. If pipeline/recheck.py was
     run, its result is used (and shown in the Recheck column); otherwise step 9's.
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
    paths = sorted(root.rglob("_gen/generation.json"))
    recs = [{**json.loads(p.read_text()), "_folder": p.parent.parent.name} for p in paths]
    if not recs:
        sys.exit(f"no generations under {root}")

    rechecked = any("recheck" in r for r in recs)
    head = (["Task", "Model", "Run"] + [f"{n} {name}" for n, name in STEPS] + RUBRIC
            + (["Recheck"] if rechecked else []) + ["Minutes", "Tokens out"])
    rows, per_step = [], defaultdict(lambda: {"n": 0, "first": 0, "final": 0})
    working = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # model -> task -> [working, total]
    for r in recs:
        by = {s["step"]: s for s in r["steps"]}
        if "2b" in by and by["2"]["passed"] is False:
            by["2"] = {**by["2"], "note": by["2b"]["note"]}
        tokens = sum((c.get("tokens_out") or 0) for s in r["steps"] for c in s.get("calls", []))
        rc = r.get("recheck", {}).get("all_tests")
        rows.append([r["task"].replace("agentdojo:", ""), r["model"], r["_folder"]]
                    + [cell(by.get(n)) for n, _ in STEPS]
                    + [r.get("rubric", {}).get(k, "-") for k in RUBRIC]
                    + ([{True: "PASS", False: "FAIL"}.get(rc, "-")] if rechecked else [])
                    + [round(r["seconds"] / 60, 1), tokens])
        ok = rc if "recheck" in r else bool(by.get("9", {}).get("passed"))
        w = working[r["model"]][r["task"].replace("agentdojo:", "")]
        w[0] += bool(ok)
        w[1] += 1
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
    tasks = sorted({t for m in working.values() for t in m})
    model_rows = []
    for m, by_task in working.items():
        tot = [sum(by_task[t][i] for t in by_task) for i in (0, 1)]
        model_rows.append([m] + [f"{by_task[t][0]}/{by_task[t][1]}" if t in by_task else "-" for t in tasks]
                          + [f"{tot[0]}/{tot[1]}"])
    report = (f"# Generation pilot\n\n{len(recs)} generations under `{root}`.\n\n"
              "## Each generation\n\nok = first try, ok(N) = after N attempts, FAIL = still failing after repairs. "
              "Rubric columns are `scripts/rubric_check.py` (offline checks).\n\n"
              + md(head, rows) + "\n\n## Per recipe step (RQ2a)\n\n"
              + md(["Step (by the model)", "Generations", "Right first try", "Right after repairs"], step_rows)
              + "\n\n## Working challenges by model (RQ1)\n\nEvery test passes"
              + (" (after `pipeline/recheck.py`)" if rechecked else "") + ".\n\n"
              + md(["Model"] + tasks + ["Total"], model_rows) + "\n")
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
