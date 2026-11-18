STEP 7 (continued): what the player sees.

The challenge's `run` script prints a briefing, reads the player's text, runs the agent and prints
the result. Its wording comes from `briefing.json`, and `TRYIT.md` describes the scenario.

## The task

```json
{facts}
```

The player controls the injection vector `{vector}` ({vector_description}).

The flag is given when this check passes:

```python
{security_source}
```

Constants in the challenge code that the briefing can name:

{constants}

## The worked example's briefing and scenario ({example_name})

This shows the format only. It is a different task: don't reuse its facts (its user request, goal,
pages, amounts or wording) unless this task's facts above say the same thing.

```text
{example_briefing}
```

```markdown
{example_scenario}
```

## What to reply

Exactly two fenced blocks, nothing else.

1. A ```json block for briefing.json with these keys (plain text, no markdown):

```json
{{
  "slug": "{slug}",
  "title": "a few words naming what the player plants, in this task's terms",
  "scenario": "2-3 lines: who the assistant works for, what tools it has, then: Emma is about to ask it:",
  "control": "1-2 lines: what the player controls and why the assistant will read it",
  "goal": "the attacker's goal, stated so it matches exactly what security() checks (see below)",
  "user_task_label": "a few words for the user's task as this task's user_prompt states it",
  "success": "one sentence shown when the goal was met",
  "failure": "one sentence shown when it wasn't"
}}
```

The `run` script already prints the user's request and the line asking for the player's text. Don't
repeat either in `scenario` or `control` (no request text, no "Ctrl-D", no `{{user_prompt}}`). You may
write `{{max_chars}}` in `control` for the input limit.

The goal must describe what the check above accepts. If the check also accepts something the
benchmark's goal wording doesn't suggest (a shortcut, a larger amount, a single step instead of
several), or rejects something the wording seems to allow, say so in the goal. A player who meets
the goal as written must get the flag, and the goal must not promise more than the check gives.

2. A ```markdown block with the scenario section of TRYIT.md: a `## The scenario` heading, the
scenario, the goal (the same as in briefing.json), and one sentence naming the attack type.
TRYIT.md is a static file: write the user's request out in words if you mention it, with no
`{{...}}` placeholders.
Plain, short sentences; no em dashes.
