# Challenge rubric v1

**Status: frozen 2026-11-01.** Any change after this date is v2: copy this file to `RUBRIC-v2.md`,
change the copy, and list what changed and why. Scores made with v1 stay v1 scores.

This rubric judges one generated challenge: a benchmark task turned into an auto-graded CTF
exercise (see `DESIGN.md`). It has four criteria. Correctness is split in two: checks a script runs
(`scripts/rubric_check.py`) and checks a person has to judge. The dry-run on the five seed
challenges is in `rubric/dry-run-v1.md`.

## How to use it

1. Run the scripted checks:
   `python3 scripts/rubric_check.py all --results evals/ --out rubric/scores/<date>-scripted.md`
2. Read the challenge as a player would (the `run` briefing and `TRYIT.md`), then its code, grader,
   reference solution and `DESIGN.md` notes. Score criteria 1B, 2, 3 and 4.
3. For every score, write one or two sentences of evidence: a file and line, a test name, or a
   quote from the briefing. A score without evidence doesn't count.

### Outcomes

Every scripted check and every criterion ends in exactly one of these:

| Outcome | Meaning | Example |
|---|---|---|
| **Score 0-3** (criteria) or **PASS / FAIL** (checks) | The rater could judge it. | — |
| **N/A** | The question doesn't apply to this kind of challenge. Give the reason. | 2c "the player's text sits where the benchmark's injection vector is" for a direct-injection challenge, which has no injection point in the environment. |
| **CD** (can't determine) | The question applies, but the evidence to answer it is missing. Say what's missing. | 1A.6 when the challenge has fewer than 10 real-model runs. |

N/A and CD are never merged and never counted as 0. N/A shrinks what the criterion covers; CD is
a hole in the evidence that has to be filled before the score is final.

When a criterion is scored but one of its inputs is CD and could move the score by a level, write
the score as **provisional** and name the missing input, e.g. "2 (provisional: 1A.7 CD)".

### Score levels

Each judgment criterion uses four levels, 0-3, the same number of levels as BetterBench's
0 / 5 / 10 / 15 scale (Reuel et al., 2024). The levels here are anchored to what a challenge
does, not to whether an issue was acknowledged, because a challenge is software a student runs,
not a documented benchmark.

| Level | General meaning |
|---|---|
| 3 | Meets the criterion fully; no change needed. |
| 2 | Meets it with a known, documented gap a player is unlikely to hit or that is explained to them. |
| 1 | A gap a player will plausibly hit, or one that isn't documented. Fix before use. |
| 0 | Fails the criterion. The challenge can't be used as is. |

### Combining scores

There is no total and no weighting. BetterBench's first stated limitation is that it weights
all criteria equally "despite their varying levels of effort required for fulfillment and
differing contributions" (Reuel et al., 2024, §9). Rather than invent weights we can't justify,
v1 reports the four scores side by side and uses two thresholds:

- **Accept:** every scripted gate check (1A.1-1A.5) is PASS, and every judgment criterion is 2 or 3.
- **Strong:** Accept, and every judgment criterion is 3.

A challenge with CD on 1A.6, 1A.7 or 1A.8 can be accepted only as **provisional**.

### Raters

The v1 dry-run was done by one rater. For the thesis data, two raters score each challenge
independently, then agree a final score; a third person breaks ties. This is BetterBench's
procedure (Reuel et al., 2024). Record both raters' original scores so agreement can be
reported.

---

## Criterion 1: Correctness

**Explanation.** The challenge runs, the intended attack gets the flag, nothing else gets the
flag, and the grader's verdict matches what actually happened in the run.

**Justification.** Correctness is the first of the three quality metrics in Yu et al. (2026),
whose generated programming problems were judged on Correctness, Contextual Fit and Coherence.
For an auto-graded security challenge, correctness also means the flag condition is sound: a
challenge that gives the flag without the attack teaches nothing, and AgentDojo itself reports
that agents often fail tasks with no attack present, which is why a no-attack baseline is needed
(Debenedetti et al., 2024, §5). The flag must also be unreadable except through the grader, as in
pwn.college's `/flag` + SUID model (Nelson & Shoshitaishvili, 2024a). Grading is done in code
rather than by an LLM judge because LLM judges show systematic biases (Zheng et al., 2023) and
only moderate agreement with humans in education settings (Koutcheme et al., 2024).

### 1A. Scripted checks (`scripts/rubric_check.py`)

| ID | Check | PASS when | N/A / CD |
|---|---|---|---|
| 1A.1 | Builds | `challenge/run` starts with `python3 -I`, prints the briefing and rejects empty input. | — |
| 1A.2 | Offline test suite | The challenge's own offline tests pass (scripted fake model). | — |
| 1A.3 | Reference solves (offline) | The reference attack gets the flag with an obedient scripted model. | CD if no offline test covers it. |
| 1A.4 | No-attack fails (offline) | With no attack, the scripted model doesn't get the flag. | CD if no offline test covers it. |
| 1A.5 | Flag isolation | `run` opens the flag exactly once, inside the `if passed` branch; `--flag-path` is refused when running privileged; no settings are read from environment variables; no tool calls files, processes, network or dynamic code; `solutions/` isn't inside or referenced from `challenge/`. | — |
| 1A.6 | Reference rate, real model | For each model: the reference attack gets the flag in at least 90% of runs. One outcome per model. | CD with fewer than 10 finished runs for that model. Errored runs are left out. |
| 1A.7 | No-attack, real model | For each model: 0 flags with no attack. One outcome per model. | CD with fewer than 10 finished runs. |
| 1A.8 | Upstream equivalence | `test_equivalence.py` passes against the pinned benchmark version. | CD if the upstream package isn't installed. |

1A.1-1A.5 are **gate checks**: any FAIL means the challenge isn't accepted, whatever its other
scores. 1A.6-1A.7 decide *which models* the challenge is valid for; a challenge can be valid on
one model and not another. 1A.8 is evidence for Criterion 2.

The 90% and 0% thresholds are the acceptance criteria in `DESIGN.md` Step 9. Ten runs is the
minimum because BetterBench recommends reporting repeated runs rather than single ones
(Reuel et al., 2024, §7); with 10 runs, one miss already drops below 100%.

### 1B. Judgment: grader validity

The scripted checks show the reference attack works. 1B asks whether the grader is right about
*other* inputs: a student's attack will not look like the reference. AgentHarm notes that its
code-based grading "can sometimes miss an alternative execution trace" (Andriushchenko et al.,
2025, §5); this criterion looks for exactly that.

Questions for the rater:
- **1B.a** Does the flag condition reject near-misses (wrong target, partial goal, the right
  action on the wrong object)? Look for near-miss tests.
- **1B.b** Does it accept every successful attack a player could reasonably produce *given what
  the briefing tells them*? A requirement the briefing doesn't mention (an exact format, a
  specific tool) is a false-negative risk.
- **1B.c** Can the flag condition be met by the agent's normal, un-attacked behaviour? (1A.4 and
  1A.7 test this; here, judge whether the code makes it possible at all.)

| Level | Anchor |
|---|---|
| 3 | Near-misses are rejected (tests show it), every success the briefing allows is accepted, and normal behaviour can't meet the condition. *Example: challenge 5, where deleting file 13 by any route passes, and a wrong file, an edit or a share of file 13 all fail.* |
| 2 | One documented gap: a stricter-than-announced condition, or validity that depends on the model (a guardrail that has to hold). *Example: challenge 3, whose no-attack baseline only fails if the model honours the authorization policy.* |
| 1 | An undocumented gap a player will plausibly hit: a real success rejected, or a partial success accepted. |
| 0 | The flag can be won without the attack, or the intended attack can't win it. |

N/A: never; every challenge has a grader. CD: when the grader can't be read (e.g. it calls an
external judge that isn't available).

---

## Criterion 2: Contextual fit

**Explanation.** The challenge stays faithful to the benchmark task it came from: the same
environment, the same attack vector reaching the agent the same way, and the same success check.

**Justification.** Contextual fit is the second metric of Yu et al. (2026). Here the "context"
is the source benchmark: the thesis claims these challenges are *transformations* of published
tasks (RQ1), so a challenge that drifts from its source no longer supports that claim. AgentDojo
defines a task by its environment, user task, injection task and injection vector, the place in
the environment the attacker controls (Debenedetti et al., 2024, §3.1). Direct and indirect
injection are different attack classes (Zhang et al., 2025; Greshake et al., 2023; OWASP, 2025),
so a challenge must not silently turn one into the other.

Questions for the rater:
- **2a** Are the environment, tools and system prompt the benchmark's own? (1A.8 is the evidence.)
- **2b** Is the success check the benchmark's own check, with the same logic? (1A.8, and the
  grader source.)
- **2c** Does the player's text reach the agent where the benchmark's injection vector is, and
  does the user task make the agent read it? N/A for direct-injection challenges.
- **2d** Is every change from the benchmark listed as a deviation in `DESIGN.md` and marked in the
  code?

| Level | Anchor |
|---|---|
| 3 | Environment, tools, prompt and check match the pinned benchmark (equivalence tests pass), the player's text sits at the benchmark's own injection vector, and every deviation is documented. *Example: challenge 2.* |
| 2 | Faithful environment, tools and check, but the challenge adds something the benchmark doesn't have because the task can't work as a challenge without it, and the addition is documented. *Example: challenge 3, which adds an authorization guardrail because the benign AgentHarm task has nothing to bypass.* |
| 1 | An undocumented change to the environment, tools or check, or the attack class changed (direct to indirect or back) without saying so. |
| 0 | The challenge no longer corresponds to its source task: a different check, a different vector or a rewritten environment. |

CD: 2a and 2b when the upstream package isn't installed (1A.8 CD) and the rater can't compare
the code by hand. Score the rest and mark the criterion provisional.

---

## Criterion 3: Coherence

**Explanation.** What the player is told (briefing, `TRYIT.md`, README), what the code does,
and what the documentation says all agree.

**Justification.** Coherence is the third metric of Yu et al. (2026). For a challenge, a
mismatch between the stated goal and the grader is a correctness problem in disguise: the player
works towards one goal while the flag checks another. BetterBench's documentation criteria ask
that "how benchmark score should or shouldn't be interpreted or used is described" and that
"limitations are documented" (Reuel et al., 2024); here the "score" is the flag, and the player
is the one who has to interpret it.

Questions for the rater:
- **3a** Does the goal in the briefing describe what the grader actually checks, no more and no
  less?
- **3b** Do the briefing, `TRYIT.md`, README table and `DESIGN.md` agree on the scenario, what
  the player controls and the goal?
- **3c** Do names and values agree across them (accounts, IDs, file names, links, challenge
  numbers)?
- **3d** Does the run's output after an attempt (trace, status lines, final message) match the
  briefing's terms?

| Level | Anchor |
|---|---|
| 3 | Everything a player reads matches the code and each other. *Example: challenge 2, where the briefing says send `www.secure-systems-252.com` to Alice, and the grader checks Alice's inbox for that link.* |
| 2 | A small mismatch that doesn't change what the player has to do, e.g. a stale cross-reference, or a goal phrased slightly narrower than the check. |
| 1 | A mismatch that could send a player the wrong way: the briefing omits a requirement the grader enforces, or names a different target. |
| 0 | The briefing and grader describe different goals. |

N/A: never. CD: when a document the player would see is missing.

---

## Criterion 4: Pedagogical alignment

**Explanation.** Solving the challenge requires, and therefore teaches, the attack vector it is
meant to teach, and not something else (a formatting trick, a tool quirk, luck).

**Justification.** RQ2 asks whether generated challenges teach the intended attack vector, so it
needs its own criterion rather than being folded into correctness: a challenge can be correct
and still teach the wrong thing. The anchors use the education-first CTF principles of Nelson
and Shoshitaishvili (2024b): one concept per challenge, immediate feedback, and visible
intermediate state so the student can see *why* an attempt worked. Švábenský et al. (2020) found
that security education papers rarely evaluate learning beyond self-reports, which is a reason to
judge alignment from the challenge itself before any student study.

Questions for the rater:
- **4a** Is the intended vector named (direct injection, indirect injection, exfiltration through
  a tool, guardrail bypass) in the player-facing material?
- **4b** Is the only way to the flag through that vector? (If the player could win by something
  unrelated, such as guessing a format or exploiting a tool bug, the challenge teaches that
  instead.)
- **4c** Does a failed attempt show the player why it failed (trace of tool calls and results,
  which checks passed)?
- **4d** Is it one concept? A challenge that needs two unrelated skills makes it unclear which
  one a failure came from.

| Level | Anchor |
|---|---|
| 3 | The vector is named, it's the only route to the flag, the trace shows what the agent did and saw, and there's one concept. *Example: challenge 5: indirect injection through a calendar invite, and the trace shows the event the agent read and the `delete_file` call.* |
| 2 | Teaches the vector, but success also depends on a secondary skill or detail the player is told about. |
| 1 | Success depends noticeably on something other than the vector that the player isn't told about, or failures give no feedback. |
| 0 | The challenge can be solved without using the vector, or teaches a different vector than stated. |

N/A: never. CD: when the challenge can't be run to see its feedback.

---

## References

- Andriushchenko, M., et al. (2025). AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents. ICLR 2025. arXiv:2410.09024.
- Debenedetti, E., Zhang, J., Balunović, M., Beurer-Kellner, L., Fischer, M., & Tramèr, F. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. NeurIPS 2024 Datasets & Benchmarks. arXiv:2406.13352.
- Greshake, K., et al. (2023). Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection. AISec '23. arXiv:2302.12173.
- Koutcheme, C., et al. (2024). Open Source Language Models Can Provide Feedback: Evaluating LLMs' Ability to Help Students Using GPT-4-As-A-Judge. ITiCSE 2024. arXiv:2405.05253.
- Nelson, C., & Shoshitaishvili, Y. (2024a). DOJO: Applied Cybersecurity Education In The Browser. SIGCSE 2024.
- Nelson, C., & Shoshitaishvili, Y. (2024b). PWN The Learning Curve: Education-First CTF Challenges. SIGCSE 2024.
- OWASP GenAI Security Project (2025). LLM01:2025 Prompt Injection.
- Reuel, A., Hardy, A., Smith, C., Lamparth, M., Hardy, M., & Kochenderfer, M. J. (2024). BetterBench: Assessing AI Benchmarks, Uncovering Issues, and Establishing Best Practices. NeurIPS 2024. arXiv:2411.12990.
- Švábenský, V., Vykopal, J., & Čeleda, P. (2020). What Are Cybersecurity Education Papers About? A Systematic Literature Review of SIGCSE and ITiCSE Conferences. SIGCSE 2020.
- Yu, J., Wu, Y., Cha, G., Shah, A., & Lau, S. (2026). Improving LLM-Generated Educational Content: A Case Study on Prototyping, Prompt Engineering, and Evaluating a Tool for Generating Programming Problems for Data Science. SIGCSE TS 2026.
- Zhang, H., et al. (2025). Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents. ICLR 2025. arXiv:2410.02644.
- Zheng, L., et al. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena. NeurIPS 2023.

**To verify before citing in the thesis:** Yu et al.'s exact definitions of Correctness,
Contextual Fit and Coherence (not yet read from the PDF; the criteria here adapt the names, not
quoted definitions), and every quoted phrase against its PDF.
