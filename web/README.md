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

GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
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
git pull && GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build   # in web/
```

Accounts, attempts and solves are in the `asc-data` volume and survive rebuilds. So do the
flag key and the NRP cache salt, so players' flags don't change. Runs that were in progress
during a restart are marked as errors, and players can just run them again.

To back up: `docker compose exec web sqlite3 /data/asc.sqlite3 .dump`, or copy the volume.

## Sign in with Google (optional)

Players can sign in with any Google account (Gmail, UCI or another school) instead of a
username and password. The login page has just the Google button. The first time someone signs
in with Google, the site then asks them to choose a username (it's shown on the scoreboard, so
it's never made from their email) and, if `ASC_INVITE_CODE` is set, the invite code. After that,
the Google button alone signs them in.

The site asks Google only for `openid email` and stores the account ID (`sub`), the email and
the Workspace domain (`hd`, empty for Gmail). The email is never shown to other players or put
in the admin exports. The login page says what is recorded, and players can delete their
account and all their data from the Account page (click your username, top right).

To allow only some organisations instead (for example only UCI accounts, with no invite code),
set `ASC_GOOGLE_DOMAINS=uci.edu`. The domain is taken from Google's `hd` claim, which only
accounts managed by that organisation have, so an `@gmail.com` address can't pass as one.

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project (or use
   one you have). Under **APIs & Services → OAuth consent screen**, choose **External**, fill
   in the app name and your email, and add only the scopes `openid`, `email` and `profile`.
   These are non-sensitive, so Google doesn't need to review the app. While the app is in
   **Testing**, only the accounts you list as test users can sign in; click **Publish app**
   when you're ready for the whole lab.
2. Under **APIs & Services → Credentials**, create an **OAuth client ID**, type **Web
   application**. Add an **Authorized redirect URI** for every address the site runs at:
   - `https://<your-domain>/api/auth/google/callback` for the deployed site
   - `http://localhost:8000/api/auth/google/callback` for testing on your laptop
3. On the server, run `web/deploy/enable-google-signin.sh` and paste the client secret when
   asked. It saves the secret, sets `ASC_GOOGLE_CLIENT_ID` and `ASC_PUBLIC_URL`, adds
   `docker-compose.google.yml`, rebuilds and checks the site. Step by step, with fixes for common
   problems: `deploy/GOOGLE-SIGNIN.md`. Locally, set the variables yourself:

   ```bash
   ASC_GOOGLE_CLIENT_ID=<id>.apps.googleusercontent.com
   ASC_GOOGLE_CLIENT_SECRET_FILE=web/secrets/google_client_secret
   ASC_PUBLIC_URL=http://localhost:8000
   ```

4. If you limit sign-in to a university domain, try one account from it first. A university's
   Google Workspace admins can block third-party apps; "Access blocked" means the app needs
   to be allowed by them, which is outside this code.

To list admins by Google account, put their email in `ASC_ADMINS`
(`ASC_ADMINS=you@gmail.com,prof@uci.edu`). Google accounts are admins only by email, never by
username, and usernames can't contain `@`, so nobody can claim an admin entry by picking a
name. A non-Gmail admin email must come from that organisation's Google Workspace (the
`hd` claim): a personal Google account registered with a `@uci.edu` address doesn't count.
To turn off username and password sign-in completely, set `ASC_PASSWORD_LOGIN=0`.

Google sign-in uses the authorization-code flow with PKCE. The server swaps the code for the
ID token directly with Google over HTTPS, then checks the token's audience, issuer, expiry,
verified email and (if set) domain. No extra Python packages are needed.

## Settings

| Variable | Default | |
|---|---|---|
| `ASC_API_KEY_FILE` / `ASC_API_KEY` | (none) | Model API key. The file form is preferred. |
| `ASC_BASE_URL` | NRP | Any OpenAI-compatible endpoint with tool calling |
| `ASC_MODELS` | `gpt-oss,gemma-small` | Models players can pick; the first is the default |
| `ASC_INVITE_CODE` / `_FILE` | (none) | Needed to sign up. If empty, anyone can sign up (a warning is logged). |
| `ASC_ADMINS` | (none) | Comma-separated Google emails (for Google accounts) or usernames (for password accounts) with the Admin page |
| `ASC_GOOGLE_CLIENT_ID` | (none) | Turns on Sign in with Google (with the secret below) |
| `ASC_GOOGLE_CLIENT_SECRET` / `_FILE` | (none) | The OAuth client secret. The file form is preferred. |
| `ASC_GOOGLE_DOMAINS` | (none) | Empty: any Google account (new accounts need the invite code, if set). Or a list of Workspace domains, e.g. `uci.edu`, to allow only those (no invite code needed). |
| `ASC_PUBLIC_URL` | `http://localhost:8000` | The site's address; the Google redirect URI is this plus `/api/auth/google/callback` |
| `ASC_PASSWORD_LOGIN` | `1` | Set `0` to allow only Google sign-in |
| `ASC_MAX_CONCURRENT` | `2` | Runs sent to the model at once |
| `ASC_HOURLY_LIMIT` | `40` | Runs per player per hour (admins exempt) |
| `ASC_DATA_DIR` | `web/data` (`/data` in Docker) | SQLite DB, flag key, cache salt |
| `ASC_COOKIE_SECURE` | `1` | Set `0` only for local plain-HTTP testing |

## Known limits of this prototype

- Passwords have no rules (this is for testing). A username in `ASC_ADMINS` belongs to whoever
  signs up with a password under it first, so either create those accounts yourself before
  sharing the invite code, or use Google sign-in and list admins by email instead.
- A password account and a Google account are separate, even for the same person; there's no
  linking. Someone switching to Google starts with a new account.
- No password reset. Players can delete their own account (Account page). An admin can delete
  one from the server:
  `docker compose exec web sqlite3 /data/asc.sqlite3 "PRAGMA foreign_keys=ON; DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username='x'); DELETE FROM views WHERE user_id=(SELECT id FROM users WHERE username='x'); DELETE FROM solves WHERE user_id=(SELECT id FROM users WHERE username='x'); DELETE FROM attempts WHERE user_id=(SELECT id FROM users WHERE username='x'); DELETE FROM users WHERE username='x';"`
- Model calls on NRP took 10–45 s each during testing, so a run can take a few minutes. The
  page shows each tool call as it happens, so players aren't watching a blank spinner.
- Only one instance of the server can run at a time (SQLite and an in-process queue). That
  is fine for a lab, not for a class of hundreds.
