"""Attack-success and latency tables (one row per model) from harness results.

    python3 scripts/stats.py challenges/01-banking-standing-order/results/runs-10

Reads every *.tsv under the folder, prints two Markdown tables, and writes them to
report.md in that folder, plus summary.tsv (paste into Google Sheets).

  Attack success:  flags, errors, time per run (median and range), model calls per run
  Latency:         time per model call (count, median, mean, 90th percentile, range)

Errored runs (timeouts, server errors) are not counted as passes or fails; they are in
the Errors column. Latency uses only runs that finished.
"""
import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def p90(xs):
    return st.quantiles(xs, n=10, method="inclusive")[8] if len(xs) > 1 else xs[0]


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = Path(sys.argv[1])
    rows = []
    for p in sorted(folder.rglob("*.tsv")):
        if p.name != "summary.tsv":
            rows += list(csv.DictReader(p.open(), delimiter="\t"))
    if not rows:
        sys.exit(f"no results under {folder}")

    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["condition"])].append(r)

    success, latency, tsv = [], [], []
    for (model, cond), rs in sorted(groups.items()):
        ok = [r for r in rs if not r.get("error")]
        errors = len(rs) - len(ok)
        served = sorted({r.get("served_model", "") for r in ok} - {""})
        flags = sum(r["passed"] == "True" for r in ok)
        secs = [float(r["seconds"]) for r in ok]
        steps = [int(r["steps"]) for r in ok]
        calls = [float(x) for r in ok for x in (r.get("call_seconds") or "").split(",") if x]
        t = f"{st.median(secs):.0f}s ({min(secs):.0f}-{max(secs):.0f}s)" if secs else "-"
        c = f"{min(steps)}-{max(steps)} (median {st.median(steps):g})" if steps else "-"
        success.append([model, ", ".join(served) or "-", cond, f"{flags}/{len(ok)}", errors, t, c])
        if calls:
            latency.append([model, cond, len(calls), f"{st.median(calls):.1f}s", f"{st.mean(calls):.1f}s",
                            f"{p90(calls):.1f}s", f"{min(calls):.1f}-{max(calls):.1f}s"])
        tsv.append([model, ", ".join(served), cond, flags, len(ok), errors,
                    st.median(secs) if secs else "", min(secs) if secs else "", max(secs) if secs else "",
                    min(steps) if steps else "", max(steps) if steps else "", len(calls),
                    round(st.median(calls), 2) if calls else "", round(st.mean(calls), 2) if calls else "",
                    round(p90(calls), 2) if calls else "", min(calls) if calls else "", max(calls) if calls else ""])

    temps = sorted({r["temperature"] for r in rows})
    head = (f"Results in `{folder}`: {len(rows)} runs, temperature {', '.join(temps)}, "
            f"runs one at a time.")
    report = "\n\n".join([
        head,
        "**Attack success**",
        table(["Model", "Server reports", "Condition", "Flag", "Errors", "Time per run, median (range)",
               "Model calls per run"], success),
        "**Latency per model call**",
        table(["Model", "Condition", "Calls", "Median", "Mean", "90th percentile", "Range"], latency)
        if latency else "(no per-call timing in these results; they were made before it was recorded)",
    ]) + "\n"
    print(report)
    (folder / "report.md").write_text(report)
    with (folder / "summary.tsv").open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model", "served_model", "condition", "flags", "runs", "errors", "run_s_median",
                    "run_s_min", "run_s_max", "calls_per_run_min", "calls_per_run_max", "model_calls",
                    "call_s_median", "call_s_mean", "call_s_p90", "call_s_min", "call_s_max"])
        w.writerows(tsv)
    print(f"Saved: {folder / 'report.md'}\n       {folder / 'summary.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
