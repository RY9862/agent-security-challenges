STEPS 3 AND 4 OF THE RECIPE: copy the environment, tools and system prompt into standalone code,
leaving out the benchmark's pipeline, attack generators and test code.

## The recipe for these steps

{recipe}

## What to write now

The first part of `challenge/challenge_env.py`. It must define, for the `{suite}` suite:

1. The data models and the environment class, as in the benchmark.
2. Every tool function, in this order, with its docstring and behaviour unchanged:
   {tool_names}
3. `TOOLS` (name -> (function, names of the environment fields it receives)), argument validation
   with pydantic models built from the tool signatures, `openai_tool_specs()`, `run_tool()`, and the
   benchmark's tool-output formatting.
4. `SYSTEM_MESSAGE`, exactly this string (shown as JSON): {system_message_json}
5. `DATA_DIR = Path(__file__).resolve().parent / "data"`, plus:
   - `injection_defaults() -> dict[str, str]`: each vector's default from `DATA_DIR / "injection_vectors.yaml"`;
   - `environment_text() -> str`: the text of `DATA_DIR / "environment.yaml"` exactly as the benchmark
     prepares it *before* it fills in the injection vectors with `str.format` (if the benchmark resolves
     `!include` and re-dumps the YAML first, do the same, with the same dumping options).

Do NOT write `load_environment`, the injection point, the user task or the checks yet: later steps
add them below your code.

The data files are copied for you to `challenge/data/` with these relative paths (first lines shown):

{data_excerpts}

## The benchmark's code

The environment class and how the suite loads its data:

```python
{environment_source}
```

The tool modules used by this suite:

{tool_sources}

## A worked example: the same file from a hand-built challenge ({example_name})

Follow its structure and conventions. Its later parts (injection point, tasks, checks) are for a
different task; ignore them for now.

```python
{example_env}
```

## What to reply

Exactly one fenced ```python block containing this first part of challenge_env.py, nothing else.
