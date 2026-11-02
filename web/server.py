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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
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
ADMINS = {u.strip().lower() for u in _env("ASC_ADMINS").split(",") if u.strip()}
MAX_CONCURRENT = int(_env("ASC_MAX_CONCURRENT", "2"))     # NRP caps parallel requests per key
HOURLY_LIMIT = int(_env("ASC_HOURLY_LIMIT", "40"))         # attempts per player per hour
COOKIE_SECURE = _env("ASC_COOKIE_SECURE", "1") != "0"      # set 0 only for plain-http local testing
PYTHON = _env("ASC_PYTHON", sys.executable)
# Same list as scripts/play.sh: always-thinking hosted models need a longer timeout.
SLOW_MODELS = {"kimi", "qwen3", "qwen3-small", "glm-5", "deepseek-v4-flash", "minimax"}

PROGRESS = "@@progress "    # runner.py's live tool-call lines on stderr
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
CREATE TABLE IF NOT EXISTS solves (
  user_id INTEGER NOT NULL REFERENCES users(id),
  challenge TEXT NOT NULL,
  attempt_id INTEGER NOT NULL,
  solved REAL NOT NULL,
  PRIMARY KEY (user_id, challenge)
);
"""

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
                    if text.startswith(PROGRESS):
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
                raise RuntimeError(f"runner crashed: {''.join(err_tail)[-400:]}")
            if "error" in result:
                raise RuntimeError(result["error"])
        except Exception as e:
            msg = str(e).replace(API_KEY, "***") if API_KEY else str(e)
            ex("UPDATE attempts SET status='error', error=?, finished=? WHERE id=?",
               (msg, time.time(), attempt_id))
            return
    passed = result["passed"]
    utd = result.get("user_task_done")
    ex("UPDATE attempts SET status='done', passed=?, user_task_done=?, result=?, finished=? WHERE id=?",
       (int(passed), None if utd is None else int(utd), json.dumps(result), time.time(), attempt_id))
    if passed:
        ex("INSERT OR IGNORE INTO solves (user_id, challenge, attempt_id, solved) VALUES (?,?,?,?)",
           (row["user_id"], row["challenge"], attempt_id, time.time()))


# ------------------------------------------------------------------ app + auth

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global db, run_slots, FLAG_KEY, CACHE_SALT
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "asc.sqlite3", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    FLAG_KEY = _persistent_secret("flag_key")
    CACHE_SALT = _persistent_secret("cache_salt")
    # Runs that were in flight when the server stopped will never finish.
    ex("UPDATE attempts SET status='error', error='server restarted during the run; try again' "
       "WHERE status IN ('queued','running')")
    run_slots = asyncio.Semaphore(MAX_CONCURRENT)
    load_challenges()
    if not API_KEY:
        print("[asc] warning: no ASC_API_KEY / ASC_API_KEY_FILE set; model calls will fail "
              "unless ASC_BASE_URL is a local Ollama", file=sys.stderr)
    if not INVITE_CODE:
        print("[asc] warning: no ASC_INVITE_CODE set; anyone who finds the site can sign up",
              file=sys.stderr)
    yield
    db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def hash_pw(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
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


def is_admin(user: sqlite3.Row) -> bool:
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


@app.post("/api/register")
def register(body: Credentials, response: Response):
    if INVITE_CODE and not hmac.compare_digest(body.invite_code.strip(), INVITE_CODE):
        raise HTTPException(403, "that invite code isn't right")
    name = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", name):
        raise HTTPException(400, "username: 3-32 letters, digits, _ . or -")
    if q1("SELECT 1 FROM users WHERE username=?", (name,)):
        raise HTTPException(409, "that username is taken")
    uid = ex("INSERT INTO users (username, pw_hash, created) VALUES (?,?,?)",
             (name, hash_pw(body.password), time.time()))
    start_session(response, uid)
    return {"ok": True}


@app.post("/api/login")
def login(body: Credentials, response: Response):
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


@app.get("/api/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        return {"user": None, "invite_required": bool(INVITE_CODE)}
    return {"user": {"username": user["username"], "admin": is_admin(user)},
            "models": MODELS, "invite_required": bool(INVITE_CODE)}


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
    aid = ex("INSERT INTO attempts (user_id, challenge, model, input, status, created) "
             "VALUES (?,?,?,?, 'queued', ?)", (user["id"], slug, model, text, time.time()))
    task = asyncio.create_task(execute(aid))
    background.add(task)
    task.add_done_callback(background.discard)
    return {"id": aid}


def _attempt_out(row: sqlite3.Row, full: bool) -> dict:
    out = {k: row[k] for k in ("id", "challenge", "model", "status", "error", "created", "started", "finished")}
    out["passed"] = None if row["passed"] is None else bool(row["passed"])
    out["user_task_done"] = None if row["user_task_done"] is None else bool(row["user_task_done"])
    out["input_preview"] = row["input"][:120]
    if row["status"] == "queued":
        out["queue_position"] = q1("SELECT COUNT(*) n FROM attempts WHERE status='queued' AND id<=?",
                                   (row["id"],))["n"]
    if full:
        out["input"] = row["input"]
        if row["status"] == "running":
            out["progress"] = json.loads(row["progress"]) if row["progress"] else []
        out["result"] = json.loads(row["result"]) if row["result"] else None
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
    return _attempt_out(row, full=True)


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
                       AVG(CASE WHEN status='done' THEN finished-started END) avg_seconds
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


@app.get("/api/admin/export.jsonl")
def admin_export(_=Depends(require_admin)):
    lines = []
    for r in q("SELECT a.*, u.username FROM attempts a JOIN users u ON u.id=a.user_id ORDER BY a.id"):
        d = {k: r[k] for k in r.keys() if k not in ("result", "user_id")}
        d["result"] = json.loads(r["result"]) if r["result"] else None
        lines.append(json.dumps(d))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=attempts.jsonl"})


@app.get("/api/health")
def health():
    return {"ok": True, "challenges": len(CHALLENGES)}


# ------------------------------------------------------------------ static front end

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})
