# Third-party code and data

This repository vendors code and data from the following projects. Check each project's
license and terms of use before redistributing.

- **AgentDojo** (ETH Zurich SPY Lab), https://github.com/ethz-spylab/agentdojo,
  version 0.1.35 (commit `089ed468`), benchmark v1.2.2. Used in challenges 01 and 02:
  environment models, tools and docstrings, environment and injection-vector YAML,
  system message, and the `utility()` / `security()` task checks.
  Debenedetti et al., "AgentDojo: A Dynamic Environment to Evaluate Prompt Injection
  Attacks and Defenses for LLM Agents", NeurIPS 2024 Datasets and Benchmarks.
- **AgentHarm** via **inspect_evals** (UK AI Security Institute),
  https://github.com/UKGovernmentBEIS/inspect_evals, commit `32b79a24`. Used in challenge 03:
  benign-split tools for the database task, its grading function and grading helpers.
  Andriushchenko et al., "AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents", ICLR 2025.

The agent loop, graders' flag handling, player input channel, the challenge-3 guardrail,
reference solutions and scripts are new.
