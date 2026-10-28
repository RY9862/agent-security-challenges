# Third-party code and data

Some code and data in this repo come from the projects below. Check their licenses and terms
before redistributing.

- **AgentDojo** (ETH Zurich SPY Lab), https://github.com/ethz-spylab/agentdojo,
  version 0.1.35 (commit `089ed468`), benchmark v1.2.2. Used in challenges 01, 02, 04 and 05 for
  the environment models, tools and docstrings, environment and injection vector YAML, system
  message, and the `utility()` / `security()` checks.
  Debenedetti et al., "AgentDojo: A Dynamic Environment to Evaluate Prompt Injection
  Attacks and Defenses for LLM Agents", NeurIPS 2024 Datasets and Benchmarks.
- **AgentHarm** from **inspect_evals** (UK AI Security Institute),
  https://github.com/UKGovernmentBEIS/inspect_evals, commit `32b79a24`. Used in challenge 03
  for the benign database task tools, its grading function and grading helpers.
  Andriushchenko et al., "AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents", ICLR 2025.

Everything else is original: the agent loop, the flag handling in the graders, the player
input, the challenge 3 guardrail, the reference solutions and the scripts.
