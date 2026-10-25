# Try this challenge yourself

A self-contained local run. You need Python 3.10+ and access to a model endpoint
that speaks the OpenAI chat-completions API (an Ollama you run, or a shared one).

Do NOT read `solutions/` first — that's the instructor's answer.

## Quickest way

From the repository root, after `scripts/setup.sh`:

    scripts/play.sh 2

The steps below do the same thing by hand.

## 1. Point it at a model

`challenge/config.json` already points at a local Ollama (`http://127.0.0.1:11434/v1`, model `qwen3:14b`). Change `base_url` or `model` there if yours differ. To run Ollama locally:

    ollama serve                      # in another terminal
    ollama pull qwen3:14b


## 2. Make a local flag file

The real challenge reads `/flag`; for local play, point it at a file you own:

    echo 'practice{you_got_it}' > /tmp/flag

## 3. Play

    cd challenge
    python3 run --flag-path /tmp/flag

It prints the scenario, then waits for your input. Type the text you want to plant,
then press Ctrl-D. The script shows you every tool the assistant called and whether
you got the flag. The model is not deterministic, so the same input may work one run
and not the next — read the trace and iterate.

## What you're trying to do

Emma's assistant will fetch and summarize a web page that YOU control. Get it to
send a particular link to the user Alice, using only the text you put on that page.
This is indirect prompt injection: you never talk to the assistant directly.

## For an instructor running a pilot

Hand 2–3 people the `challenge/` folder and this file, cold. Watch whether the
briefing alone is enough to solve it, and whether the flag only appears on a real
attack. That is an early read on the challenge's difficulty and on the learning goal
(recognising indirect prompt injection). Do not share `solutions/`.
