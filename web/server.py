"""Web front end for the agent prompt-injection challenges (temporary, until they move to Codesafe).

Players sign up with an invite code, pick a challenge, type their input, and the server
runs the real challenge grader against the model in a separate process (web/runner.py).
Everything the terminal version prints (tool trace, final reply, verdict, flag) comes
back as JSON for the page to show.

Run it:   uvicorn server:app --app-dir web --host 127.0.0.1 --port 8000
Settings come from environment variables. See web/README.md.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


# ------------------------------------------------------------------ settings

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _read_secret(name: str) -> str:
    """ASC_FOO, or the contents of the file named by ASC_FOO_FILE."""
    if _env(name):
        return _env(name)
    if _env(name + "_FILE"):
        return Path(_env(name + "_FILE")).expanduser().read_text().strip()
    return ""


DATA_DIR = Path(_env("ASC_DATA_DIR", str(HERE / "data"))).expanduser()
CHALLENGES_DIR = Path(_env("ASC_CHALLENGES_DIR", str(ROOT / "challenges")))
BASE_URL = _env("ASC_BASE_URL", "https://ellm.nrp-nautilus.io/v1")
API_KEY = _read_secret("ASC_API_KEY")
MODELS = [m.strip() for m in _env("ASC_MODELS", "gpt-oss,gemma-small").split(",") if m.strip()]
INVITE_CODE = _read_secret("ASC_INVITE_CODE")
ADMINS = {u.strip().lower() for u in _env("ASC_ADMINS").split(",") if u.strip()}   # usernames or Google emails
MAX_CONCURRENT = int(_env("ASC_MAX_CONCURRENT", "2"))     # NRP caps parallel requests per key
HOURLY_LIMIT = int(_env("ASC_HOURLY_LIMIT", "40"))         # attempts per player per hour
COOKIE_SECURE = _env("ASC_COOKIE_SECURE", "1") != "0"      # set 0 only for plain-http local testing
PYTHON = _env("ASC_PYTHON", sys.executable)

# Sign in with Google (optional). Turned on by setting the client ID and secret from a Google
# Cloud "OAuth client ID" (type: Web application). See web/README.md, "Sign in with Google".
GOOGLE_CLIENT_ID = _env("ASC_GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _read_secret("ASC_GOOGLE_CLIENT_SECRET")
GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
# Default: any Google account can sign in; new accounts need the invite code if one is set.
# To allow only some organisations instead, list their Google Workspace domains, e.g.
# "uci.edu". That is checked against the ID token's "hd" claim, which Google sets only for
# accounts managed by that organisation (an @gmail.com address can't fake it), and accounts
# from a listed domain don't need the invite code.
GOOGLE_DOMAINS = {d.strip().lower() for d in _env("ASC_GOOGLE_DOMAINS").split(",") if d.strip()}
# The site's public address, used to build the redirect URI registered with Google,
# e.g. https://challenges.example.edu (no trailing slash). Locally: http://localhost:8000
PUBLIC_URL = _env("ASC_PUBLIC_URL", "http://localhost:8000").rstrip("/")
GOOGLE_REDIRECT_URI = PUBLIC_URL + "/api/auth/google/callback"
# Set 0 to allow only Google sign-in (no username/password sign-up or login).
PASSWORD_LOGIN = _env("ASC_PASSWORD_LOGIN", "1") != "0"
# Same list as scripts/play.sh: always-thinking hosted models need a longer timeout.
SLOW_MODELS = {"kimi", "qwen3", "qwen3-small", "glm-5", "deepseek-v4-flash", "minimax"}

sys.path.insert(0, str(HERE))
from runner import PARTIAL, PROGRESS, _token_totals, folder_hash  # noqa: E402
SESSION_COOKIE = "asc_session"
SESSION_DAYS = 30


def _persistent_secret(name: str) -> str:
    """A random value made once and kept in the data folder (flag key, cache salt)."""
    p = DATA_DIR / name
    if not p.exists():
        p.write_text(secrets.token_urlsafe(48))
        p.chmod(0o600)
    return p.read_text().strip()


# ------------------------------------------------------------------ database

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  challenge TEXT NOT NULL,
  model TEXT NOT NULL,
  input TEXT NOT NULL,
  status TEXT NOT NULL,          -- queued | running | done | error
  passed INTEGER,                -- 1/0 once done
  user_task_done INTEGER,
  result TEXT,                   -- runner JSON
  progress TEXT,                 -- tool calls so far, JSON list, while running
  error TEXT,
  created REAL NOT NULL,
  started REAL,
  finished REAL
);
CREATE INDEX IF NOT EXISTS attempts_user ON attempts(user_id, challenge);
-- In-flight Google sign-ins: the random "state" sent to Google and the PKCE verifier.
CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash TEXT PRIMARY KEY,
  verifier TEXT NOT NULL,
  invite_ok INTEGER NOT NULL,
  created REAL NOT NULL
);
-- Google accounts that signed in for the first time and haven't picked a username yet.
CREATE TABLE IF NOT EXISTS google_signups (
  token_hash TEXT PRIMARY KEY,
  sub TEXT NOT NULL,
  email TEXT NOT NULL,
  hd TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS solves (
  user_id INTEGER NOT NULL REFERENCES users(id),
  challenge TEXT NOT NULL,
  attempt_id INTEGER NOT NULL,
  solved REAL NOT NULL,
  PRIMARY KEY (user_id, challenge)
);
-- When each player first and last opened each challenge page (time spent, for research).
CREATE TABLE IF NOT EXISTS views (
  user_id INTEGER NOT NULL REFERENCES users(id),
  challenge TEXT NOT NULL,
  first_viewed REAL NOT NULL,
  last_viewed REAL NOT NULL,
  view_count INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, challenge)
);
"""

# Columns added after the first version. Existing databases get them on startup.
MIGRATIONS = [
    ("attempts", "challenge_version", "TEXT"),   # hash of the challenge/ folder the run used
    ("attempts", "settings", "TEXT"),            # run settings, JSON (no key, no cache salt)
    ("attempts", "tokens_in", "INTEGER"),
    ("attempts", "tokens_out", "INTEGER"),
    ("users", "google_sub", "TEXT"),             # Google account ID ("sub"); NULL for password users
    ("users", "email", "TEXT"),                  # Google email, for admins and contact
    ("users", "google_hd", "TEXT"),              # Workspace domain ("hd" claim); "" for Gmail
]

db: sqlite3.Connection


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return db.execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
    return db.execute(sql, args).fetchone()


def ex(sql: str, args: tuple = ()) -> int:
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ challenges

CHALLENGES: dict[str, dict] = {}


def _scenario(chal: Path) -> str:
    """The '## The scenario' section of TRYIT.md (the rest is terminal instructions)."""
    p = chal / "TRYIT.md"
    if not p.exists():
        return ""
    m = re.search(r"^## The scenario\s*\n(.*?)(?=^## |\Z)", p.read_text(), re.S | re.M)
    return m.group(1).strip() if m else ""


def _git_commit() -> str:
    """The repo commit the server is running from (ASC_GIT_COMMIT in Docker, which has no .git)."""
    if _env("ASC_GIT_COMMIT"):
        return _env("ASC_GIT_COMMIT")
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "challenges", "web"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "") if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


GIT_COMMIT = ""


def load_challenges() -> None:
    manifest = json.loads((HERE / "challenges.json").read_text())
    for chal in sorted(CHALLENGES_DIR.iterdir()):
        if not (chal / "challenge" / "run").exists():
            continue
        info = manifest.get(chal.name, {})
        if info.get("hidden"):
            continue
        proc = subprocess.run([PYTHON, "-I", str(HERE / "runner.py"), "meta", str(chal)],
                              capture_output=True, text=True, timeout=60)
        try:
            meta = json.loads(proc.stdout)
        except json.JSONDecodeError:
            meta = {"error": proc.stderr.strip()[-500:]}
        if "error" in meta:
            print(f"[asc] skipping {chal.name}: {meta['error']}", file=sys.stderr)
            continue
        num = chal.name.split("-", 1)[0]
        CHALLENGES[chal.name] = {
            "slug": chal.name,
            "number": int(num) if num.isdigit() else 0,
            "title": (meta["title"] or chal.name).capitalize(),
            "briefing": meta["briefing"],
            "max_input_chars": meta["max_input_chars"],
            "scenario": _scenario(chal),
            # Text from Ryan's README table and `run` output (web/challenges.json); no fallbacks
            # of our own, so an unlisted challenge just shows fewer fields.
            "type": info.get("type", ""),
            "control": info.get("control", ""),
            "goal": info.get("goal", ""),
            "input_label": meta.get("prompt", ""),
            "user_task_label": info.get("user_task_label"),
            "success": info.get("success", ""),
            "failure": info.get("failure", ""),
            "config": json.loads((chal / "challenge" / "config.json").read_text()),
            "path": str(chal),
        }
    print(f"[asc] loaded {len(CHALLENGES)} challenges: {', '.join(CHALLENGES)}", file=sys.stderr)


def flag_for(slug: str, user_id: int) -> str:
    """Per-player flag, so a flag pasted in a group chat shows who it came from."""
    mac = hmac.new(FLAG_KEY.encode(), f"{slug}:{user_id}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"asc{{{slug.split('-', 1)[0]}_{mac}}}"


def run_config(chal: dict, model: str) -> dict:
    """The challenge's own config.json with the server's model settings on top, as play.sh does."""
    cfg = dict(chal["config"])
    cfg.update(model=model, base_url=BASE_URL, seed=None, max_retries=8)
    if API_KEY:
        cfg["api_key"] = API_KEY
    if "nrp-nautilus" in BASE_URL:
        cfg["extra_body"] = {**cfg.get("extra_body", {}), "cache_salt": CACHE_SALT}
    if model in SLOW_MODELS:
        cfg["timeout_s"] = 900.0
    return cfg


# ------------------------------------------------------------------ attempt queue

run_slots: asyncio.Semaphore
background: set[asyncio.Task] = set()


async def execute(attempt_id: int) -> None:
    row = q1("SELECT * FROM attempts WHERE id=?", (attempt_id,))
    chal = CHALLENGES[row["challenge"]]
    payload = json.dumps({"text": row["input"], "config": run_config(chal, row["model"])})
    hard_limit = 1800 if row["model"] in SLOW_MODELS else 600
    partial: dict = {"challenge_version": None, "model_calls": [], "tool_calls": []}
    async with run_slots:
        ex("UPDATE attempts SET status='running', started=? WHERE id=?", (time.time(), attempt_id))
        try:
            proc = await asyncio.create_subprocess_exec(
                PYTHON, "-I", str(HERE / "runner.py"), "run", chal["path"],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=2**22)
            proc.stdin.write(payload.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            progress: list = []
            err_tail: list[str] = []

            async def read_stderr():
                async for line in proc.stderr:
                    text = line.decode(errors="replace")
                    if text.startswith(PARTIAL):
                        item = json.loads(text[len(PARTIAL):])
                        if item["kind"] == "challenge_version":
                            partial["challenge_version"] = item["data"]
                        else:
                            partial[item["kind"] + "s"].append(item["data"])
                    elif text.startswith(PROGRESS):
                        progress.append(json.loads(text[len(PROGRESS):]))
                        ex("UPDATE attempts SET progress=? WHERE id=?", (json.dumps(progress), attempt_id))
                    else:
                        err_tail[:] = (err_tail + [text])[-20:]

            out_task = asyncio.create_task(proc.stdout.read())
            try:
                await asyncio.wait_for(asyncio.gather(read_stderr(), proc.wait()), hard_limit)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"the run took longer than {hard_limit // 60} minutes and was stopped")
            out = await out_task
            try:
                result = json.loads(out)
            except json.JSONDecodeError:
                how = (f"was killed by signal {-proc.returncode}" if proc.returncode < 0
                       else f"exited with code {proc.returncode}")
                tail = "".join(err_tail).strip()[-400:]
                raise RuntimeError(f"the run stopped unexpectedly (runner {how})" + (f": {tail}" if tail else ""))
            if "error" in result:
                raise RuntimeError(result["error"])
        except Exception as e:
            msg = str(e).replace(API_KEY, "***") if API_KEY else str(e)
            # Keep whatever the run got through before it failed, for investigation.
            tokens = _token_totals(partial["model_calls"])
            saved = {"partial": True, **partial, "tokens": tokens}
            ex("UPDATE attempts SET status='error', error=?, finished=?, result=?, tokens_in=?, "
               "tokens_out=?, challenge_version=COALESCE(?, challenge_version) WHERE id=?",
               (msg, time.time(), json.dumps(saved, default=str).replace(API_KEY, "***") if API_KEY
                else json.dumps(saved, default=str), tokens["input"], tokens["output"],
                partial["challenge_version"], attempt_id))
            return
    passed = result["passed"]
    utd = result.get("user_task_done")
    tokens = result.get("tokens") or {}
    ex("UPDATE attempts SET status='done', passed=?, user_task_done=?, result=?, finished=?, "
       "tokens_in=?, tokens_out=?, progress=NULL, challenge_version=COALESCE(?, challenge_version) "
       "WHERE id=?",
       (int(passed), None if utd is None else int(utd), json.dumps(result), time.time(),
        tokens.get("input"), tokens.get("output"), result.get("challenge_version"), attempt_id))
    if passed:
        ex("INSERT OR IGNORE INTO solves (user_id, challenge, attempt_id, solved) VALUES (?,?,?,?)",
           (row["user_id"], row["challenge"], attempt_id, time.time()))


# ------------------------------------------------------------------ app + auth

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global db, run_slots, FLAG_KEY, CACHE_SALT, GIT_COMMIT
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "asc.sqlite3", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_google ON users(google_sub) "
               "WHERE google_sub IS NOT NULL")
    db.commit()
    db.execute("PRAGMA journal_mode=WAL")
    FLAG_KEY = _persistent_secret("flag_key")
    CACHE_SALT = _persistent_secret("cache_salt")
    # Runs that were in flight when the server stopped will never finish.
    ex("UPDATE attempts SET status='error', error='server restarted during the run; try again' "
       "WHERE status IN ('queued','running')")
    run_slots = asyncio.Semaphore(MAX_CONCURRENT)
    load_challenges()
    GIT_COMMIT = _git_commit()
    if not API_KEY:
        print("[asc] warning: no ASC_API_KEY / ASC_API_KEY_FILE set; model calls will fail "
              "unless ASC_BASE_URL is a local Ollama", file=sys.stderr)
    if not INVITE_CODE and PASSWORD_LOGIN:
        print("[asc] warning: no ASC_INVITE_CODE set; anyone who finds the site can sign up",
              file=sys.stderr)
    if GOOGLE_ENABLED:
        print(f"[asc] Google sign-in on: {('domains ' + ', '.join(sorted(GOOGLE_DOMAINS))) if GOOGLE_DOMAINS else 'any Google account' + (' (invite code for new accounts)' if INVITE_CODE else '')}, "
              f"redirect URI {GOOGLE_REDIRECT_URI}", file=sys.stderr)
    elif not PASSWORD_LOGIN:
        print("[asc] warning: ASC_PASSWORD_LOGIN=0 but Google sign-in isn't configured; "
              "nobody can sign in", file=sys.stderr)
    yield
    db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def hash_pw(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    if "$" not in stored:          # Google-only account: no password to check
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt)), stored)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request) -> Optional[sqlite3.Row]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return q1("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
              "WHERE s.token_hash=? AND s.expires>?", (_token_hash(token), time.time()))


def require_user(request: Request) -> sqlite3.Row:
    # Cross-site form posts can't send a JSON body, and SameSite=Lax keeps the cookie off
    # cross-site fetches, so requiring JSON on writes is enough CSRF protection here.
    if request.method == "POST" and "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "send JSON")
    user = current_user(request)
    if not user:
        raise HTTPException(401, "sign in first")
    return user


# Google itself owns these addresses, so a verified email there is proof of the address.
CONSUMER_GOOGLE_DOMAINS = {"gmail.com", "googlemail.com"}


def _google_owns_email(email: str, hd: str) -> bool:
    """Whether a Google sign-in proves the person owns this address. A Google account can be
    registered with a non-Gmail address (e.g. someone@uci.edu as a personal Google account,
    without UCI's Workspace), so outside Gmail we also require the "hd" claim, which Google sets
    only for accounts the organisation that owns the domain manages."""
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in CONSUMER_GOOGLE_DOMAINS or (hd or "").lower() == domain


def is_admin(user: sqlite3.Row) -> bool:
    # Google accounts are admins only by their email, never by username, so a Google user can't
    # pick an unclaimed username from ASC_ADMINS. Usernames can't contain "@", so nobody can
    # claim an admin email by signing up with a password.
    if user["google_sub"]:
        email = (user["email"] or "").lower()
        return bool(email) and email in ADMINS and _google_owns_email(email, user["google_hd"] or "")
    return user["username"].lower() in ADMINS


def require_admin(user=Depends(require_user)) -> sqlite3.Row:
    if not is_admin(user):
        raise HTTPException(403, "admins only")
    return user


def start_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    ex("INSERT INTO sessions VALUES (?,?,?)",
       (_token_hash(token), user_id, time.time() + SESSION_DAYS * 86400))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)   # no rules: this is a testing prototype
    invite_code: str = ""


def _check_new_username(raw: str) -> str:
    name = raw.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", name):
        raise HTTPException(400, "username: 3-32 letters, digits, _ . or -")
    if q1("SELECT 1 FROM users WHERE username=?", (name,)):
        raise HTTPException(409, "that username is taken")
    return name


@app.post("/api/register")
def register(body: Credentials, response: Response):
    if not PASSWORD_LOGIN:
        raise HTTPException(403, "sign up with Google")
    if INVITE_CODE and not hmac.compare_digest(body.invite_code.strip(), INVITE_CODE):
        raise HTTPException(403, "that invite code isn't right")
    name = _check_new_username(body.username)
    uid = ex("INSERT INTO users (username, pw_hash, created) VALUES (?,?,?)",
             (name, hash_pw(body.password), time.time()))
    start_session(response, uid)
    return {"ok": True}


@app.post("/api/login")
def login(body: Credentials, response: Response):
    if not PASSWORD_LOGIN:
        raise HTTPException(403, "sign in with Google")
    user = q1("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if not user or not check_pw(body.password, user["pw_hash"]):
        raise HTTPException(401, "wrong username or password")
    start_session(response, user["id"])
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        ex("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


def _auth_options() -> dict:
    return {"invite_required": bool(INVITE_CODE), "password_login": PASSWORD_LOGIN,
            "google": GOOGLE_ENABLED, "google_domains": sorted(GOOGLE_DOMAINS)}


@app.get("/api/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        return {"user": None, **_auth_options()}
    return {"user": {"username": user["username"], "admin": is_admin(user),
                     "google": bool(user["google_sub"]), "email": user["email"] or ""},
            "models": MODELS, **_auth_options()}


@app.post("/api/account/delete")
def delete_account(body: dict, response: Response, user=Depends(require_user)):
    """Delete the signed-in player's account and everything recorded about them: sessions,
    attempts (inputs, traces, model output), solves and page views."""
    if str(body.get("confirm", "")).strip().lower() != user["username"].lower():
        raise HTTPException(400, "type your username to confirm")
    if q1("SELECT 1 FROM attempts WHERE user_id=? AND status IN ('queued','running')", (user["id"],)):
        raise HTTPException(409, "wait for your running attempt to finish first")
    uid = user["id"]
    with db:   # all or nothing
        for table in ("sessions", "views", "solves", "attempts"):
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    response.delete_cookie(SESSION_COOKIE)
    print(f"[asc] account {uid} deleted by its owner", file=sys.stderr)
    return {"ok": True}


# ------------------------------------------------------------------ Sign in with Google
# OpenID Connect authorization-code flow with PKCE. The browser goes to Google and comes back
# to /api/auth/google/callback with a one-time code; the server swaps the code for an ID token
# directly with Google over HTTPS and reads the account from it.

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_COOKIE = "asc_oauth"
SIGNUP_COOKIE = "asc_google_signup"   # a first-time Google sign-in that still needs a username
OAUTH_TTL = 600   # seconds a sign-in may take


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _jwt_claims(token: str) -> dict:
    """Claims of a JWT, without checking the signature. Only used on an ID token the server
    received straight from Google's token endpoint over HTTPS, which OpenID Connect Core
    3.1.3.7 allows in place of a signature check; aud, iss and exp are still checked."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _auth_error(msg: str) -> RedirectResponse:
    r = RedirectResponse("/?auth_error=" + urllib.parse.quote(msg), status_code=303)
    r.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
    return r


@app.get("/api/auth/google/start")
def google_start(invite: str = ""):
    if not GOOGLE_ENABLED:
        raise HTTPException(404, "Google sign-in isn't set up on this server")
    ex("DELETE FROM oauth_states WHERE created<?", (time.time() - OAUTH_TTL,))
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    invite_ok = not INVITE_CODE or hmac.compare_digest(invite.strip(), INVITE_CODE)
    ex("INSERT INTO oauth_states VALUES (?,?,?,?)", (_token_hash(state), verifier, int(invite_ok), time.time()))
    params = {
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI, "response_type": "code",
        "scope": "openid email", "state": state, "prompt": "select_account",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    if len(GOOGLE_DOMAINS) == 1:
        params["hd"] = next(iter(GOOGLE_DOMAINS))   # only a hint for the account chooser; checked below
    r = RedirectResponse(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params), status_code=303)
    # Ties the sign-in to this browser, so a link with someone else's code can't sign you in.
    # SameSite=None: the way back from Google can be a cross-site redirect chain (e.g. through
    # a university's own login page), on which some browsers drop Lax cookies. The cookie holds
    # only the random state, is HttpOnly, path-limited and lives 10 minutes. Browsers require
    # Secure with None, so plain-http local testing keeps Lax.
    r.set_cookie(OAUTH_COOKIE, state, max_age=OAUTH_TTL, httponly=True,
                 samesite="none" if COOKIE_SECURE else "lax",
                 secure=COOKIE_SECURE, path="/api/auth/google")
    return r


def _google_exchange(code: str, verifier: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print(f"[asc] Google token exchange failed: {e.code} {detail}", file=sys.stderr)
        raise RuntimeError("Google didn't accept the sign-in; try again") from None


@app.get("/api/auth/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if not GOOGLE_ENABLED:
        raise HTTPException(404, "Google sign-in isn't set up on this server")
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    row = q1("SELECT * FROM oauth_states WHERE state_hash=?", (_token_hash(state),)) if state else None
    if row:
        ex("DELETE FROM oauth_states WHERE state_hash=?", (row["state_hash"],))   # one use only
    if error:
        return _auth_error("Google sign-in was cancelled")
    if not row or row["created"] < time.time() - OAUTH_TTL:
        return _auth_error("that sign-in link expired; try again")
    if not cookie or not hmac.compare_digest(cookie, state):
        print(f"[asc] Google sign-in refused: state cookie {'missing' if not cookie else 'mismatched'}",
              file=sys.stderr)
        return _auth_error("your browser didn't keep the sign-in cookie. Allow cookies for this site, "
                           "then start again from the Sign in with Google button")
    try:
        tokens = _google_exchange(code, row["verifier"])
        claims = _jwt_claims(tokens["id_token"])
    except (RuntimeError, KeyError, ValueError, IndexError, urllib.error.URLError) as e:
        return _auth_error(str(e) if isinstance(e, RuntimeError) else "couldn't reach Google; try again")

    if claims.get("aud") != GOOGLE_CLIENT_ID or claims.get("iss") not in ("https://accounts.google.com",
                                                                          "accounts.google.com"):
        return _auth_error("that sign-in wasn't meant for this site")
    if float(claims.get("exp", 0)) < time.time():
        return _auth_error("that sign-in expired; try again")
    sub, email = str(claims.get("sub", "")), str(claims.get("email", "")).lower()
    if not sub or not email or not claims.get("email_verified"):
        return _auth_error("your Google account has no verified email address")
    hd = str(claims.get("hd", "")).lower()
    if GOOGLE_DOMAINS and hd not in GOOGLE_DOMAINS:
        allowed = " or ".join("@" + d for d in sorted(GOOGLE_DOMAINS))
        return _auth_error(f"sign in with your {allowed} Google account")

    user = q1("SELECT * FROM users WHERE google_sub=?", (sub,))
    if not user:
        if not GOOGLE_DOMAINS and not row["invite_ok"]:
            return _auth_error("new accounts need the invite code: type it in, then click Sign in with Google")
        # First sign-in: the player picks their own username (it's shown on the scoreboard), so
        # we don't make one from their email address.
        ex("DELETE FROM google_signups WHERE created<?", (time.time() - OAUTH_TTL,))
        token = secrets.token_urlsafe(32)
        ex("INSERT INTO google_signups VALUES (?,?,?,?,?)", (_token_hash(token), sub, email, hd, time.time()))
        r = RedirectResponse("/?google_signup=1", status_code=303)
        r.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
        r.set_cookie(SIGNUP_COOKIE, token, max_age=OAUTH_TTL, httponly=True, samesite="lax",
                     secure=COOKIE_SECURE, path="/api/auth/google")
        return r
    uid = user["id"]
    if user["email"] != email or (user["google_hd"] or "") != hd:
        ex("UPDATE users SET email=?, google_hd=? WHERE id=?", (email, hd, uid))
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
    start_session(r, uid)
    return r


def _pending_signup(request: Request) -> sqlite3.Row:
    token = request.cookies.get(SIGNUP_COOKIE, "")
    row = q1("SELECT * FROM google_signups WHERE token_hash=? AND created>?",
             (_token_hash(token), time.time() - OAUTH_TTL)) if token else None
    if not row:
        raise HTTPException(404, "that sign-in expired; click Sign in with Google again")
    return row


@app.get("/api/auth/google/signup")
def google_signup_info(request: Request):
    return {"email": _pending_signup(request)["email"]}


class GoogleSignup(BaseModel):
    username: str = Field(min_length=1, max_length=64)


@app.post("/api/auth/google/signup")
def google_signup(body: GoogleSignup, request: Request, response: Response):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "send JSON")
    row = _pending_signup(request)
    name = _check_new_username(body.username)
    existing = q1("SELECT id FROM users WHERE google_sub=?", (row["sub"],))   # finished in another tab
    if existing:
        uid = existing["id"]
    else:
        try:
            uid = ex("INSERT INTO users (username, pw_hash, created, google_sub, email, google_hd) "
                     "VALUES (?,?,?,?,?,?)", (name, "", time.time(), row["sub"], row["email"], row["hd"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "that username is taken") from None
    ex("DELETE FROM google_signups WHERE token_hash=?", (row["token_hash"],))
    response.delete_cookie(SIGNUP_COOKIE, path="/api/auth/google")
    start_session(response, uid)
    return {"ok": True}


# ------------------------------------------------------------------ challenges + attempts

def _card(c: dict) -> dict:
    keys = ("slug", "number", "title", "type", "control", "goal")
    return {k: c[k] for k in keys}


@app.get("/api/challenges")
def list_challenges(user=Depends(require_user)):
    solved = {r["challenge"] for r in q("SELECT challenge FROM solves WHERE user_id=?", (user["id"],))}
    counts = {r["challenge"]: r["n"] for r in q("SELECT challenge, COUNT(*) n FROM solves GROUP BY challenge")}
    tries = {r["challenge"]: r["n"] for r in
             q("SELECT challenge, COUNT(*) n FROM attempts WHERE user_id=? GROUP BY challenge", (user["id"],))}
    return [{**_card(c), "solved": c["slug"] in solved, "solves": counts.get(c["slug"], 0),
             "my_attempts": tries.get(c["slug"], 0)} for c in CHALLENGES.values()]


def _get_challenge(slug: str) -> dict:
    if slug not in CHALLENGES:
        raise HTTPException(404, "no such challenge")
    return CHALLENGES[slug]


@app.get("/api/challenges/{slug}")
def get_challenge(slug: str, user=Depends(require_user)):
    c = _get_challenge(slug)
    now = time.time()
    ex("INSERT INTO views (user_id, challenge, first_viewed, last_viewed) VALUES (?,?,?,?) "
       "ON CONFLICT(user_id, challenge) DO UPDATE SET last_viewed=excluded.last_viewed, "
       "view_count=view_count+1", (user["id"], slug, now, now))
    solve = q1("SELECT * FROM solves WHERE user_id=? AND challenge=?", (user["id"], slug))
    keys = ("briefing", "scenario", "max_input_chars", "input_label", "user_task_label")
    return {**_card(c), **{k: c[k] for k in keys},
            "solved": bool(solve), "flag": flag_for(slug, user["id"]) if solve else None}


class AttemptIn(BaseModel):
    text: str
    model: str = ""


@app.post("/api/challenges/{slug}/attempts")
async def create_attempt(slug: str, body: AttemptIn, user=Depends(require_user)):
    c = _get_challenge(slug)
    text = body.text[:-1] if body.text.endswith("\n") else body.text   # same as the CLI
    if not text.strip():
        raise HTTPException(400, "your input is empty")
    if len(text) > c["max_input_chars"]:
        raise HTTPException(400, f"your input is {len(text)} characters; the limit is {c['max_input_chars']}")
    model = body.model or MODELS[0]
    if model not in MODELS:
        raise HTTPException(400, "that model isn't available")
    if q1("SELECT 1 FROM attempts WHERE user_id=? AND status IN ('queued','running')", (user["id"],)):
        raise HTTPException(409, "you already have a run going; wait for it to finish")
    recent = q1("SELECT COUNT(*) n FROM attempts WHERE user_id=? AND created>?",
                (user["id"], time.time() - 3600))["n"]
    if recent >= HOURLY_LIMIT and not is_admin(user):
        raise HTTPException(429, f"you've used your {HOURLY_LIMIT} runs for this hour; try again later")
    cfg = run_config(c, model)
    settings = {k: v for k, v in cfg.items() if k not in ("api_key", "extra_body")}
    settings["extra_body"] = {k: v for k, v in cfg.get("extra_body", {}).items() if k != "cache_salt"}
    settings["server_commit"] = GIT_COMMIT
    aid = ex("INSERT INTO attempts (user_id, challenge, model, input, status, created, "
             "challenge_version, settings) VALUES (?,?,?,?, 'queued', ?, ?, ?)",
             (user["id"], slug, model, text, time.time(),
              folder_hash(Path(c["path"]) / "challenge"),   # provisional; the runner's replaces it
              json.dumps(settings)))
    task = asyncio.create_task(execute(aid))
    background.add(task)
    task.add_done_callback(background.discard)
    return {"id": aid}


def _attempt_out(row: sqlite3.Row, full: bool) -> dict:
    out = {k: row[k] for k in ("id", "challenge", "model", "status", "error", "created", "started", "finished")}
    out["passed"] = None if row["passed"] is None else bool(row["passed"])
    out["user_task_done"] = None if row["user_task_done"] is None else bool(row["user_task_done"])
    out["input_preview"] = row["input"][:120]
    out["tokens_in"], out["tokens_out"] = row["tokens_in"], row["tokens_out"]
    if row["status"] == "queued":
        out["queue_position"] = q1("SELECT COUNT(*) n FROM attempts WHERE status='queued' AND id<=?",
                                   (row["id"],))["n"]
    if full:
        out["input"] = row["input"]
        if row["status"] == "running":
            out["progress"] = json.loads(row["progress"]) if row["progress"] else []
        out["result"] = json.loads(row["result"]) if row["result"] else None
        out["challenge_version"] = row["challenge_version"]
        out["settings"] = json.loads(row["settings"]) if row["settings"] else None
        if out["passed"]:
            out["flag"] = flag_for(row["challenge"], row["user_id"])
            out["message"] = CHALLENGES.get(row["challenge"], {}).get("success")
        elif out["passed"] is False:
            out["message"] = CHALLENGES.get(row["challenge"], {}).get("failure")
    return out


@app.get("/api/attempts/{aid}")
def get_attempt(aid: int, user=Depends(require_user)):
    row = q1("SELECT * FROM attempts WHERE id=?", (aid,))
    if not row or (row["user_id"] != user["id"] and not is_admin(user)):
        raise HTTPException(404, "no such attempt")
    out = _attempt_out(row, full=True)
    if not is_admin(user):
        # Kept for investigation only. Players see the same trace as the terminal version;
        # the model's raw responses include its reasoning text, which would make it easier.
        for k in ("settings", "challenge_version"):
            out.pop(k, None)
        if out.get("result"):
            for k in ("model_calls", "challenge_version"):
                out["result"].pop(k, None)
    return out


@app.get("/api/challenges/{slug}/attempts")
def my_attempts(slug: str, user=Depends(require_user)):
    rows = q("SELECT * FROM attempts WHERE user_id=? AND challenge=? ORDER BY id DESC LIMIT 100",
             (user["id"], slug))
    return [_attempt_out(r, full=False) for r in rows]


@app.get("/api/scoreboard")
def scoreboard(user=Depends(require_user)):
    rows = q("""SELECT u.username, COUNT(s.challenge) solved, MAX(s.solved) last,
                       (SELECT COUNT(*) FROM attempts a WHERE a.user_id=u.id) attempts
                FROM users u LEFT JOIN solves s ON s.user_id=u.id
                GROUP BY u.id ORDER BY solved DESC, last ASC""")
    per = {}
    for r in q("SELECT u.username, s.challenge FROM solves s JOIN users u ON u.id=s.user_id"):
        per.setdefault(r["username"], []).append(r["challenge"])
    return [{"username": r["username"], "solved": r["solved"], "last": r["last"],
             "attempts": r["attempts"], "challenges": sorted(per.get(r["username"], []))} for r in rows]


# ------------------------------------------------------------------ admin (for whoever is tuning the challenges)

@app.get("/api/admin/stats")
def admin_stats(_=Depends(require_admin)):
    rows = q("""SELECT challenge, model, COUNT(*) runs, SUM(passed=1) flags,
                       SUM(status='error') errors, COUNT(DISTINCT user_id) players,
                       AVG(CASE WHEN status='done' THEN finished-started END) avg_seconds,
                       AVG(tokens_in) avg_tokens_in, AVG(tokens_out) avg_tokens_out,
                       SUM(tokens_in) total_tokens_in, SUM(tokens_out) total_tokens_out
                FROM attempts GROUP BY challenge, model ORDER BY challenge, model""")
    queue = q1("SELECT SUM(status='queued') queued, SUM(status='running') running FROM attempts")
    return {"by_challenge_model": [dict(r) for r in rows],
            "queued": queue["queued"] or 0, "running": queue["running"] or 0,
            "max_concurrent": MAX_CONCURRENT}


@app.get("/api/admin/attempts")
def admin_attempts(challenge: str = "", username: str = "", passed: str = "", limit: int = 100,
                   _=Depends(require_admin)):
    sql = "SELECT a.*, u.username FROM attempts a JOIN users u ON u.id=a.user_id WHERE 1=1"
    args: list = []
    if challenge:
        sql += " AND a.challenge=?"; args.append(challenge)
    if username:
        sql += " AND u.username=?"; args.append(username)
    if passed in ("0", "1"):
        sql += " AND a.passed=?"; args.append(int(passed))
    sql += " ORDER BY a.id DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    return [{**_attempt_out(r, full=False), "username": r["username"]} for r in q(sql, tuple(args))]


PLAYER_PROGRESS_SQL = """
SELECT u.username, v.challenge, v.first_viewed, v.last_viewed, v.view_count,
       COUNT(a.id) attempts, SUM(a.passed=1) flags, SUM(a.status='error') errors,
       s.solved first_solve,
       s.solved - v.first_viewed seconds_to_solve,
       CASE WHEN s.attempt_id IS NULL THEN NULL ELSE
         (SELECT COUNT(*) FROM attempts b WHERE b.user_id=v.user_id AND b.challenge=v.challenge
            AND b.id<=s.attempt_id) END attempts_to_solve,
       SUM(a.tokens_in) tokens_in, SUM(a.tokens_out) tokens_out,
       SUM(a.finished - a.started) run_seconds
FROM views v JOIN users u ON u.id=v.user_id
LEFT JOIN attempts a ON a.user_id=v.user_id AND a.challenge=v.challenge
LEFT JOIN solves s ON s.user_id=v.user_id AND s.challenge=v.challenge
GROUP BY v.user_id, v.challenge ORDER BY u.username, v.challenge
"""


@app.get("/api/admin/players")
def admin_players(_=Depends(require_admin)):
    """One row per player and challenge they opened: first/last visit, attempts, solve time.
    seconds_to_solve is wall-clock time from first opening the challenge to the first flag,
    not time actively spent on it."""
    return [dict(r) for r in q(PLAYER_PROGRESS_SQL)]


def _jsonl(rows: list[dict], filename: str) -> PlainTextResponse:
    body = "".join(json.dumps(r) + "\n" for r in rows)
    return PlainTextResponse(body, media_type="application/x-ndjson",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/admin/export.jsonl")
def admin_export(_=Depends(require_admin)):
    """Every attempt: input, settings, challenge version, tokens, timing, full result
    (trace, grader verdict, and every raw model response under result.model_calls)."""
    rows = []
    for r in q("SELECT a.*, u.username FROM attempts a JOIN users u ON u.id=a.user_id ORDER BY a.id"):
        d = {k: r[k] for k in r.keys() if k not in ("result", "settings", "progress", "user_id")}
        d["settings"] = json.loads(r["settings"]) if r["settings"] else None
        d["result"] = json.loads(r["result"]) if r["result"] else None
        rows.append(d)
    return _jsonl(rows, "attempts.jsonl")


@app.get("/api/admin/players.jsonl")
def admin_players_export(_=Depends(require_admin)):
    return _jsonl([dict(r) for r in q(PLAYER_PROGRESS_SQL)], "players.jsonl")


@app.get("/api/health")
def health():
    return {"ok": True, "challenges": len(CHALLENGES)}


# ------------------------------------------------------------------ static front end

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})
