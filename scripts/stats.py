"""Attack-success and latency tables (one row per model) from harness results.

    python3 scripts/stats.py evals/2026-10-31-1021-sweep-ch1/01-banking-standing-order/runs-10

Reads every *.tsv under the folder, prints two Markdown tables, and writes them to
report.md in that folder, plus summary.tsv (paste into Google Sheets).

  Attack success:  flags, errors, time per run (median and range), model calls per run
  Latency:         time per model call (count, median, mean, 90th percentile, range)
  Response speed:  output tokens per model call and output tokens per second
                   (total tokens / total call time, and median of per-call rates)

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

    success, latency, speed, tsv = [], [], [], []
    for (model, cond), rs in sorted(groups.items()):
        ok = [r for r in rs if not r.get("error")]
        errors = len(rs) - len(ok)
        served = sorted({r.get("served_model", "") for r in ok} - {""})
        flags = sum(r["passed"] == "True" for r in ok)
        secs = [float(r["seconds"]) for r in ok]
        steps = [int(r["steps"]) for r in ok]
        calls = [float(x) for r in ok for x in (r.get("call_seconds") or "").split(",") if x]
        pairs = [(float(c), int(k)) for r in ok if r.get("call_tokens")
                 for c, k in zip(r["call_seconds"].split(","), r["call_tokens"].split(",")) if float(c) > 0]
        rates = [k / c for c, k in pairs if k]
        tok_s = sum(k for _, k in pairs) / sum(c for c, _ in pairs) if rates else None
        t = f"{st.median(secs):.0f}s ({min(secs):.0f}-{max(secs):.0f}s)" if secs else "-"
        c = f"{min(steps)}-{max(steps)} (median {st.median(steps):g})" if steps else "-"
        success.append([model, ", ".join(served) or "-", cond, f"{flags}/{len(ok)}", errors, t, c])
        if calls:
            latency.append([model, cond, len(calls), f"{st.median(calls):.1f}s", f"{st.mean(calls):.1f}s",
                            f"{p90(calls):.1f}s", f"{min(calls):.1f}-{max(calls):.1f}s"])
        if rates:
            toks = [k for _, k in pairs]
            speed.append([model, cond, len(pairs), f"{st.mean(toks):.0f}", f"{tok_s:.1f}",
                          f"{st.median(rates):.1f}", f"{min(rates):.1f}-{max(rates):.1f}"])
        tsv.append([model, ", ".join(served), cond, flags, len(ok), errors,
                    st.median(secs) if secs else "", min(secs) if secs else "", max(secs) if secs else "",
                    min(steps) if steps else "", max(steps) if steps else "", len(calls),
                    round(st.median(calls), 2) if calls else "", round(st.mean(calls), 2) if calls else "",
                    round(p90(calls), 2) if calls else "", min(calls) if calls else "", max(calls) if calls else "",
                    round(st.mean(k for _, k in pairs), 1) if rates else "", round(tok_s, 1) if rates else "",
                    round(st.median(rates), 1) if rates else ""])

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
        "**Response speed (output tokens)**",
        table(["Model", "Condition", "Calls", "Tokens per call, mean", "Tokens/s overall", "Tokens/s, median call",
               "Tokens/s range"], speed)
        if speed else "(no token counts in these results; they were made before they were recorded)",
    ]) + "\n"
    print(report)
    (folder / "report.md").write_text(report)
    with (folder / "summary.tsv").open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model", "served_model", "condition", "flags", "runs", "errors", "run_s_median",
                    "run_s_min", "run_s_max", "calls_per_run_min", "calls_per_run_max", "model_calls",
                    "call_s_median", "call_s_mean", "call_s_p90", "call_s_min", "call_s_max",
                    "out_tokens_mean", "tok_per_s_overall", "tok_per_s_median"])
        w.writerows(tsv)
    print(f"Saved: {folder / 'report.md'}\n       {folder / 'summary.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
