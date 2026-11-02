# Web front end (temporary)

A small website so lab members can play the challenges in a browser instead of the
terminal, until the challenges move into Codesafe. Players sign up with an invite code,
pick a challenge, type their input, and watch the agent's tool calls as they happen. The
page shows the same trace, final reply, verdict and flag as `scripts/play.sh`.

```
browser ──HTTPS──▶ Caddy ──▶ uvicorn (web/server.py) ──spawns──▶ web/runner.py <challenge>
                                  │                                     │
                              SQLite (/data)                 challenge/grader.py grade()
                                                                        │
                                                               NRP (one shared key)
```

- **Grading is unchanged.** `runner.py` imports each challenge's own `grader.grade()` in a
  fresh process (the challenges share module names, so they can't share one interpreter).
  It does what `play.sh` does: the challenge's `config.json`, with the model, base URL and
  key set by the server and an unseeded run.
- **One API key, kept on the server.** The key is read from a file and passed to the runner
  on stdin. It never goes to the browser, into the database, or into command-line
  arguments, and it is masked in error messages. Players can only send their input text and
  pick from `ASC_MODELS`. They can't change the endpoint or any other setting.
- **Protecting the key's quota.** Runs go through a queue with at most
  `ASC_MAX_CONCURRENT` at a time (NRP limits parallel requests per key). Each player can
  have one run going and `ASC_HOURLY_LIMIT` runs per hour. Runs are killed after 10
  minutes (30 for reasoning models).
- **Flags are per player:** `asc{NN_<hmac of challenge and user>}`. A solve is recorded as
  soon as the grader passes, so there's nothing to submit. The scoreboard shows who solved
  what.
- **Admin page** (users listed in `ASC_ADMINS`): the success rate per challenge and model
  from real player attempts, every attempt with its full trace, and a JSONL export.
  Useful for seeing which inputs students try and how often the challenge resists them.
- **Nothing from `solutions/`, `tests/` or `SOLUTIONS-SPOILERS.md` is served** or copied
  into the Docker image (see `/.dockerignore`).

The briefing, title and input limit come from each challenge's `run` and grader, and the
scenario text comes from its `TRYIT.md`, so editing a challenge updates the site after a
restart. Card text such as "You control" and "Goal" is in `web/challenges.json`. A new
`challenges/06-*` folder shows up automatically, with generic card text until you add an
entry.

## Run it locally

```bash
scripts/setup.sh                                   # once
.venv/bin/pip install -r web/requirements.txt      # once
ASC_API_KEY_FILE=~/.config/agent-security-challenges/nrp_key \
ASC_INVITE_CODE=test ASC_ADMINS=yourname ASC_COOKIE_SECURE=0 \
  .venv/bin/uvicorn server:app --app-dir web --port 8000
```

Open http://127.0.0.1:8000, create an account named `yourname` with invite code `test`.
`ASC_COOKIE_SECURE=0` is only for plain-HTTP local testing. Leave it unset in deployment.

## Deploy on a VM (Docker + Caddy)

Needs Docker with the compose plugin, Caddy, and a DNS name pointing at the VM, with ports
80 and 443 open.

```bash
git clone <this repo> /opt/agent-security-challenges
cd /opt/agent-security-challenges/web

cp .env.example .env && nano .env        # invite code, admins, models
mkdir -p secrets && nano secrets/nrp_key # paste the NRP key (yours or Ryan's)
sudo chown 1000 secrets/nrp_key && sudo chmod 400 secrets/nrp_key   # the container runs as uid 1000

docker compose up -d --build
curl -s 127.0.0.1:8000/api/health        # {"ok":true,"challenges":5}

sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the hostname first
sudo systemctl reload caddy
```

The app only listens on `127.0.0.1:8000`, so Caddy is the only thing reachable from
outside, and it handles HTTPS. To run it without Docker, see `deploy/asc-web.service`.

Run **one** uvicorn worker (the default). The queue and its concurrency limit live in the
process, so several workers would each send `ASC_MAX_CONCURRENT` runs to NRP at once.

### Updating

```bash
git pull && docker compose up -d --build   # in web/
```

Accounts, attempts and solves are in the `asc-data` volume and survive rebuilds. So do the
flag key and the NRP cache salt, so players' flags don't change. Runs that were in progress
during a restart are marked as errors, and players can just run them again.

To back up: `docker compose exec web sqlite3 /data/asc.sqlite3 .dump`, or copy the volume.

## Settings

| Variable | Default | |
|---|---|---|
| `ASC_API_KEY_FILE` / `ASC_API_KEY` | (none) | Model API key. The file form is preferred. |
| `ASC_BASE_URL` | NRP | Any OpenAI-compatible endpoint with tool calling |
| `ASC_MODELS` | `gpt-oss,gemma-small` | Models players can pick; the first is the default |
| `ASC_INVITE_CODE` / `_FILE` | (none) | Needed to sign up. If empty, anyone can sign up (a warning is logged). |
| `ASC_ADMINS` | (none) | Comma-separated usernames with the Admin page |
| `ASC_MAX_CONCURRENT` | `2` | Runs sent to the model at once |
| `ASC_HOURLY_LIMIT` | `40` | Runs per player per hour (admins exempt) |
| `ASC_DATA_DIR` | `web/data` (`/data` in Docker) | SQLite DB, flag key, cache salt |
| `ASC_COOKIE_SECURE` | `1` | Set `0` only for local plain-HTTP testing |

## Known limits of this prototype

- Passwords have no rules (this is for testing), and admin rights come from the username
  alone: whoever first signs up with a name in `ASC_ADMINS` is an admin. Before sharing the
  invite code, choose admin usernames that aren't obvious and create those accounts yourself.

- No password reset. An admin can delete a user with
  `sqlite3 /data/asc.sqlite3 "DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username='x')"`
  and then delete the user's row, or just ask them to make a new account.
- Model calls on NRP took 10–45 s each during testing, so a run can take a few minutes. The
  page shows each tool call as it happens, so players aren't watching a blank spinner.
- Only one instance of the server can run at a time (SQLite and an in-process queue). That
  is fine for a lab, not for a class of hundreds.
