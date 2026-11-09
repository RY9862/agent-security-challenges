# Generation pipeline: benchmark task + recipe in, challenge out

`generate.py` turns an AgentDojo task pair into a challenge by having an LLM follow `DESIGN.md`
one step at a time. Each step the model does is checked against the pinned benchmark before the
next one starts, and every prompt, reply and check is saved, so you can see which recipe steps a
model does reliably and which need a person (RQ2a).

| Recipe step (DESIGN.md) | Done by | Checked by |
|---|---|---|
| 1 Pick a task | you (`--task`) | the task is loaded from agentdojo 0.1.35 |
| 2 Viability | model (JSON verdict) | its chosen injection vector must be one the user task reads |
| 3-4 Port environment, tools, system prompt | model | `test_equivalence.py -k port`: tool names, descriptions, parameters, system prompt; no benchmark imports |
| 5 Injection point | model | `-k injection`: default environment equals upstream; player text reaches the agent byte for byte |
| 6 Agent loop | script (copies `agent.py`, `grader.py`, `run`, `harness.py` from challenge 2) | - |
| 7 Flag grader | model | `-k tasks`: `utility()`/`security()` match upstream on the benchmark's ground-truth calls |
| 7 Player text | model (`briefing.json`, `TRYIT.md` scenario) | `run` starts and prints the briefing |
| 8 Reference solution | model | `test_end_to_end.py`: names the attack's targets, gets the flag with an obedient fake model |
| 9 Tests | script | all tests, then `scripts/rubric_check.py` (1A.1-1A.5, 1A.8) |

The model writes `challenge_env.py` in three parts (steps 3, 5, 7) and the player-facing text.
The shared files are copied unchanged, and the model's text goes into `briefing.json`, which
`run` reads as data, so nothing the model writes ends up in the flag-handling code. Each model
step sees its section of `DESIGN.md`, the benchmark's source for that task, the code written so
far, and a hand-built seed challenge as a worked example (the seed from the same suite, never the
seed for the same task).

A failed check is sent back to the model with the error, up to `--repairs` times (default 1).
The report shows whether each step was right first try, after repairs, or not at all.

**The checks run the code the model wrote.** Run the pipeline somewhere you'd run untrusted code
(a VM, a container, or a lab machine you don't mind), not on a machine with secrets you care about.

## Setup

```bash
.venv/bin/pip install -r requirements.txt -r pipeline/requirements.txt   # adds agentdojo==0.1.35
source scripts/use-nrp.sh                                                # NRP key, as for sweeps
```

## Run

```bash
python3 pipeline/generate.py --model gpt-oss --runs 3 \
  --task agentdojo:slack:user_task_2:injection_task_3
python3 pipeline/report.py generated/          # table of every generation so far
```

Options: `--repairs N`, `--temperature`, `--reasoning-effort low|medium|high`, `--max-tokens`,
`--example 01` (force the worked example), `--out DIR`. `--task` can be repeated.

Output, per run (git ignores `generated/`; copy what you want to keep into SORAB2C):

```
generated/<model>/<task>/run-<k>/
  challenge/ solutions/ tests/ TRYIT.md harness.py     the generated challenge
  _gen/steps/<step>/prompt-N.md reply-N.md check-N.txt  every attempt
  _gen/generation.json                                 per-step outcome, tokens, timing
  _gen/rubric.md                                       scripted rubric checks
```

A generated challenge is played like a seed: `cd generated/.../run-1/challenge && python3 run --flag-path /tmp/flag`,
with the model set in `challenge/config.json`. For real-model acceptance runs, use its
`harness.py --model gpt-oss --base-url $BASE_URL --out <folder>`.

## Pilot (Week 7)

```bash
source scripts/use-nrp.sh
python3 pipeline/generate.py --model gpt-oss --runs 3 \
  --task agentdojo:slack:user_task_3:injection_task_1 \
  --task agentdojo:slack:user_task_2:injection_task_3 \
  --task agentdojo:banking:user_task_0:injection_task_6 \
  --task agentdojo:travel:user_task_0:injection_task_3
python3 pipeline/report.py generated/
```

1. `slack u3 x i1` is challenge 2's own task: a sanity check against a known-good answer
   (its worked example is a different seed, so the answer isn't in the prompt).
2. `slack u2 x i3`: a new injection task in a suite the seeds cover.
3. `banking u0 x i6`: a new pair in banking; the player's text goes into a bill file.
4. `travel u0 x i3`: a suite no seed uses (28 tools), the hardest test.

Then score the generations that pass the scripted checks with the rubric's judgment criteria
(1B, 2, 3, 4), the same way as `rubric/dry-run-v1.md`.

## Limits of this version

- AgentDojo only. AgentHarm challenges need a guardrail designed by a person (DESIGN.md Step 9.5).
- Injection tasks graded from the agent's tool-call trace (`security_from_traces`, e.g. Slack
  `injection_task_5`) are refused with a message: the challenge grader checks the final environment.
- The offline checks prove the plumbing and that the reference names the attack's targets. Whether
  the reference beats a real model is rubric check 1A.6: run the generated challenge's `harness.py`.
- Only links, IBANs, email addresses and quoted IDs count as attack targets; a goal with none
  (e.g. "book this hotel") skips that check.
