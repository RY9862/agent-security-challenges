"""Pool every results TSV for a challenge into one table per model and condition.

    python3 scripts/summarize.py challenges/02-slack-phishing-link [filename-filter]

Errored runs (e.g. a malformed tool call the server rejected) are counted separately;
report them, don't drop them.
"""
import csv, glob, sys
from collections import defaultdict

if len(sys.argv) < 2:
    sys.exit(__doc__)
pat = sys.argv[2] if len(sys.argv) > 2 else ""
agg = defaultdict(lambda: [0, 0, 0, 0, set()])   # flags, runs, errors, refusals, seeds
for p in sorted(glob.glob(f"{sys.argv[1]}/results/*{pat}*.tsv")):
    for x in csv.DictReader(open(p), delimiter="\t"):
        a = agg[(x["model"], x["condition"])]
        if x.get("error"):
            a[2] += 1
            continue
        a[0] += x["passed"] == "True"; a[1] += 1
        a[3] += x.get("refusal_like") == "True"; a[4].add(int(x["seed"]))
if not agg:
    sys.exit(f"no results under {sys.argv[1]}/results/")
print(f"{'model':20s} {'condition':10s} {'flags':>9s} {'refusals':>9s} {'errors':>7s}  seeds")
for (m, c), (k, n, e, r, s) in sorted(agg.items()):
    rng = f"{min(s)}-{max(s)} ({len(s)})" if s else "-"
    print(f"{m:20s} {c:10s} {k:4d}/{n:<4d} {r:9d} {e:7d}  {rng}")
