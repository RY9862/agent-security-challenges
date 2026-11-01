"""One report for a whole eval folder (made by sweep.sh or bench.sh).

    python3 scripts/eval_report.py evals/2026-10-31-1021-sweep-ch1-2

Rebuilds report.md and summary.tsv (scripts/stats.py) in every folder under it that has
run TSVs, then writes at the top of the eval folder:

  report.md    a table of latency and response speed per model, pooled over all its
               challenges, then each folder's report
  summary.tsv  every folder's summary.tsv rows, with a column saying which folder
"""
import csv
import statistics as st
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

STATS = Path(__file__).with_name("stats.py")


def run_tsvs(folder):
    return [p for p in folder.glob("*.tsv") if p.name != "summary.tsv"]


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ev = Path(sys.argv[1]).resolve()
    folders = sorted({p.parent for p in ev.rglob("*.tsv") if p.name != "summary.tsv" and p.parent != ev})
    if not folders:
        sys.exit(f"no results under {ev}")

    calls = defaultdict(lambda: ([], [], []))   # model -> (seconds, tokens, seconds of calls with tokens)
    for f in folders:
        subprocess.run([sys.executable, str(STATS), str(f)], check=True, stdout=subprocess.DEVNULL)
        for p in run_tsvs(f):
            for r in csv.DictReader(p.open(), delimiter="\t"):
                if r.get("error") or not r.get("call_seconds"):
                    continue
                secs = [float(x) for x in r["call_seconds"].split(",")]
                toks = [int(x) for x in r["call_tokens"].split(",")] if r.get("call_tokens") else []
                calls[r["model"]][0].extend(secs)
                if toks:
                    calls[r["model"]][1].extend(toks)
                    calls[r["model"]][2].extend(secs)

    rows = []
    for m, (secs, toks, tok_secs) in calls.items():
        tps = sum(toks) / sum(tok_secs) if toks else None
        rows.append((tps or 0, [m, len(secs), f"{st.mean(secs):.1f}s", f"{st.median(secs):.1f}s",
                                f"{tps:.1f}" if toks else "-", f"{st.mean(toks):.0f}" if toks else "-"]))
    rows = [r for _, r in sorted(rows, key=lambda x: -x[0])]

    rel = lambda f: f.relative_to(ev).as_posix()
    parts = [f"# {ev.name}", f"Folders: {', '.join(f'`{rel(f)}`' for f in folders)}"]
    if (ev / "models.txt").exists():
        parts.append("Models: " + ", ".join(f"`{m}`" for m in (ev / "models.txt").read_text().split()))
    parts += ["## Latency and response speed, all challenges together",
              "Per model call, runs that finished. Fastest output first.",
              "\n".join(["| Model | Calls | Mean latency | Median latency | Output tokens/s | Tokens per call |",
                         "|---|---|---|---|---|---|"] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])]
    for f in folders:
        parts += [f"## {rel(f)}", (f / "report.md").read_text().strip()]
    (ev / "report.md").write_text("\n\n".join(parts) + "\n")

    with (ev / "summary.tsv").open("w", newline="") as out:
        w = csv.writer(out, delimiter="\t")
        header = None
        for f in folders:
            with (f / "summary.tsv").open() as src:
                r = csv.reader(src, delimiter="\t")
                h = next(r)
                if header is None:
                    header = ["folder"] + h
                    w.writerow(header)
                w.writerows([rel(f)] + row for row in r)
    print(f"Saved: {ev / 'report.md'}\n       {ev / 'summary.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
