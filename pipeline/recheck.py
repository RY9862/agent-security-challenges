"""Re-run the offline checks on challenges that were already generated, with the current tests.

    python3 pipeline/recheck.py generated-wk8/        # then: python3 pipeline/report.py generated-wk8/

Use it after a check is fixed, so earlier generations don't have to be regenerated. For each
generated challenge it copies the current test templates into tests/, runs the full test suite and
the scripted rubric checks, and saves the result in _gen/generation.json under "recheck" (and
replaces "rubric"). The step outcomes recorded during generation are kept as they were: they show
what the model did with the feedback it got at the time.
"""
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generate  # noqa: E402


def first_failure(text: str) -> str:
    failed = [l.replace("FAILED tests/", "") for l in text.splitlines() if l.startswith("FAILED")]
    return failed[0][:160] if failed else (text.splitlines() or [""])[-1]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "generated")
    gens = sorted(p.parent.parent for p in root.rglob("_gen/generation.json"))
    if not gens:
        sys.exit(f"no generations under {root}")
    ok_n = 0
    for out in gens:
        rec_path = out / "_gen" / "generation.json"
        rec = json.loads(rec_path.read_text())
        label = f"{rec['model']} {rec['task'].replace('agentdojo:', '')} {out.name}"
        if not (out / "challenge" / "run").exists() or not (out / "solutions" / "reference.txt").exists():
            rec["recheck"] = {"checks_version": generate.PROMPT_VERSION, "all_tests": None,
                              "note": "generation stopped before a full challenge existed"}
            rec_path.write_text(json.dumps(rec, indent=1))
            print(f"-     {label}: not a full challenge, skipped", flush=True)
            continue
        for t in ("test_equivalence.py", "test_end_to_end.py"):
            shutil.copy(generate.TEMPLATES / t, out / "tests" / t)
        try:
            p = subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                               cwd=out, capture_output=True, text=True, timeout=600)
            ok, tail = p.returncode == 0, generate.tail(p.stdout + p.stderr, 12)
        except subprocess.TimeoutExpired:
            ok, tail = False, "tests timed out after 600 s"
        (out / "_gen" / "recheck.txt").write_text(tail + "\n")
        _, row = generate.rubric(out)
        rec["rubric"] = row
        rec["recheck"] = {"checks_version": generate.PROMPT_VERSION, "all_tests": ok,
                          "date": dt.datetime.now().isoformat(timespec="seconds"),
                          "note": "" if ok else first_failure(tail)}
        rec_path.write_text(json.dumps(rec, indent=1))
        ok_n += ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": {rec['recheck']['note']}"), flush=True)
    print(f"\n{ok_n}/{len(gens)} generated challenges pass every test with checks version {generate.PROMPT_VERSION}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
