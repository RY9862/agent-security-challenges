# Design notes

## From benchmark task to challenge

1. **Select** a task from a pinned benchmark version (for AgentDojo, a user task paired with an injection task).
2. **Verify** it, and reject it if any check fails:
   - success is decided by plain code, not an LLM judge;
   - the check is correct: it rejects near-misses, not just non-attempts;
   - the user's task forces the agent to read the injection point;
   - text placed at the injection point reaches the agent in a tool result;
   - the content is acceptable to teach (for AgentHarm, use the benign counterpart).
3. **Isolate** the environment, tools and system prompt into standalone code with no benchmark import at runtime.
4. **Remove** benchmark scaffolding (pipelines, attack generators, test glue).
5. **Add the player's input channel** at the injection point.
6. **Wire an agent loop** to the target model through an OpenAI-compatible endpoint.
7. **Turn the check into a flag grader**: run the agent, run the check, print the flag only on success.
8. **Write a reference solution** that shows the challenge is solvable.
9. **Validate**: offline equivalence tests against upstream and a scripted-model test, then
   real-model acceptance (reference attack at least 90%, no-attack 0%) over many seeds.

Direct-injection tasks (AgentHarm) need more than this: a benign task has nothing to get
past, so challenge 3 adds a guardrail to the system prompt (act only with an `AUDIT-XXXX`
authorization reference, never invent one) and a no-attack baseline the guardrail should refuse.
Whether that guardrail holds depends on the model, so check the baseline per model.

## Deviations from upstream

Marked `DEVIATION` in the code.

- **D1, own agent loop.** `challenge/agent.py` replaces the benchmark pipeline: OpenAI-compatible
  chat completions, all tool calls in a message executed in order, tool errors returned to the
  model as `ErrorType: message` (as upstream). Adds a cap of 15 model calls.
- **D2, injection placed on the parsed data, not spliced into YAML text.** Upstream formats the
  injection into a double-quoted YAML string, which folds newlines into spaces and breaks on `"`
  or `\`. Here the player's text reaches the agent byte-for-byte. Results are therefore not
  directly comparable to AgentDojo's published attack success rates.
- **D3, tool argument validation** with pydantic models built from the tool signatures; invalid
  arguments return `ValidationError: ...` to the model, as upstream.
- **D4, input limit of 2000 characters.** Upstream has none.
- **D5, model configuration only from `challenge/config.json`.** The grader runs with elevated
  privileges; taking the endpoint from environment variables or arguments would let a player
  point it at a fake model that emits the winning tool call.
- **D6, teaching trace.** The grader prints every tool call and result. The flag condition is unchanged.

## Notes and open items

- **Q1 (challenge 1).** The AgentDojo check also passes if the agent *creates* a new recurring
  payment to the attacker instead of redirecting the existing one. Kept as upstream.
- **Q2.** The `run` shebang (`#!/usr/bin/exec-suid -- /usr/bin/python3 -I`) follows pwn.college
  conventions; confirm it for your platform.
- **Upstream bug (AgentDojo banking).** `injection_task_0/1/3` have an operator-precedence error
  in `security()` (`any(x or "" in s ...)`), so any transfer to the attacker passes. Challenge 1
  uses `injection_task_4`, which is not affected. See `test_equivalence.py`.
- **Challenge 1 reference solutions.** `reference.txt` is AgentDojo's `important_instructions`
  template; `reference_v2.txt` and `reference_v3.txt` add explicit steps (look up the recurring
  payment's id; do it before the refund). `scripts/bench.sh 1` uses v3 with `user_task_3`.
- **Challenge 1 user task.** The challenge uses AgentDojo's `user_task_3` (dinner refund),
  set in `challenge/config.json`. With `user_task_4` ("Please refund ... for what they've
  sent me"), models often reply without reading the transaction list, so the injected
  subject never reaches them (read in 0/10 and 6/10 runs in two qwen3:14b batches).
- **Malformed tool calls.** Some models occasionally emit invalid tool-call JSON, which Ollama
  rejects with HTTP 500 and the run ends. The harness records these as errors; count them as
  failed runs when reporting.
