"""NEXUS — cyberpunk tabletop RPG with a Backboard-powered GM that remembers everything."""
import asyncio
import hashlib
import inspect
import json
import os
import random
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backboard import BackboardClient

load_dotenv()

# ---------------------------------------------------------------------------
# EDIT ME: per-mode model routing (llm_provider + model_name sent per message)
# ---------------------------------------------------------------------------
MODE_MODELS = {
    "story":  {"llm_provider": "anthropic", "model_name": "claude-haiku-5-5"},
    "combat": {"llm_provider": "anthropic", "model_name": "claude-haiku-5-5"},
    "netrun": {"llm_provider": "anthropic", "model_name": "claude-haiku-5-5"},
}
# Optional .env override for every mode: MODEL_PROVIDER / MODEL_NAME
for _cfg in MODE_MODELS.values():
    if os.environ.get("MODEL_PROVIDER"):
        _cfg["llm_provider"] = os.environ["MODEL_PROVIDER"]
    if os.environ.get("MODEL_NAME"):
        _cfg["model_name"] = os.environ["MODEL_NAME"]
DEFAULT_MODE = "story"

MAX_TURNS = 5  # player prompts per run
DEV_UNLIMITED = os.environ.get("DEV_UNLIMITED") == "1"  # lifts the one-run-per-day limit
MAX_DAILY_MESSAGES = int(os.environ.get("MAX_DAILY_MESSAGES") or 3000)  # global cap, all players
IP_LIMIT, IP_WINDOW = 20, 600  # messages per IP per 10 minutes
PROMPT_MIN, PROMPT_MAX = 3, 280

SYSTEM_PROMPT = (
    "You are NEXUS, the game master of a cyberpunk tabletop RPG set in Neo-Hamilton, 2077, "
    "a megacity built over dead steel mills. The ruling megacorp is Delta Corp, whose logo is "
    "an impossible triangle. The player has HP, Humanity, creds, a class and stats. Whenever an action is risky, you MUST "
    "call the roll_d20 tool yourself before narrating the result; never ask the player to "
    "roll. 15+ succeeds, 10-14 succeeds at a cost, under 10 fails, natural 20 and 1 are "
    "dramatic. Cyberware costs creds and 5-20 Humanity; use install_cyberware. Below 50 "
    "Humanity your narration becomes glitchy and paranoid; below 25 you are hostile. Remember "
    "the player's choices, allies, enemies, and debts and bring them back later. Keep replies "
    "under 120 words, second person. End every reply with a clear choice, EXCEPT on the final "
    "turn, which must end the story. Stay in character. "
    "Call set_location whenever the scene moves to a new place. "
    "Every run is a mission. At the start of every run, call set_mission with ONE concrete, "
    "achievable goal that fits Neo-Hamilton 2077, and vary it each run (heist, rescue, sabotage, "
    f"escape, delivery). The mission must be completable or failable in {MAX_TURNS} player actions: "
    "one objective, at most 2 locations, no multi-part quests. "
    f"This run is exactly {MAX_TURNS} player prompts long. Treat it as a short story with a beginning, "
    "middle and end, and never continue it after the final turn. Keep every reply moving the player toward or away from that goal. Hidden "
    "[system: ...] notes give you the turn count and the player's sheet: never quote them or lecture about the turn limit, "
    "but warn the player naturally in the story when 1 turn remains. When told the run is over, "
    "call submit_rating and be honest: S only for a clever, fully achieved goal; D and F for clear "
    "failure. In the verdict and your final lines, mention the player's earlier choices and what "
    "the world will remember."
)

LOCATIONS = ["alley", "street", "corp_lobby", "clinic", "rooftop", "server_room"]
GRADES = ["S", "A", "B", "C", "D", "F"]
CREDS_BY_GRADE = {"S": 400, "A": 300, "B": 200, "C": 120, "D": 60, "F": 0}

# Classes: original archetypes, stats 1-3 (summing to 7)
CLASSES = {
    "breaker": {"name": "BREAKER", "blurb": "Mill-floor muscle. Hits hard, takes hits.",
                "stats": {"muscle": 3, "tech": 1, "sly": 1, "mouth": 2}},
    "splicer": {"name": "SPLICER", "blurb": "Wire-witch. Machines tell you their secrets.",
                "stats": {"muscle": 1, "tech": 3, "sly": 2, "mouth": 1}},
    "ghost":   {"name": "GHOST", "blurb": "Harbour-fog thief. Never seen twice.",
                "stats": {"muscle": 1, "tech": 2, "sly": 3, "mouth": 1}},
    "hustler": {"name": "HUSTLER", "blurb": "Bridge-market fixer. Talks anyone into anything.",
                "stats": {"muscle": 2, "tech": 1, "sly": 1, "mouth": 3}},
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_d20",
            "description": "Roll a 20-sided die for any risky action. 15+ succeeds, 10-14 succeeds at a cost, under 10 fails. Natural 20/1 are dramatic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "What the roll is for"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "change_stat",
            "description": "Change a player stat by a delta. Use negative amounts for damage/costs, positive for healing/gains.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stat": {"type": "string", "enum": ["hp", "humanity", "creds"]},
                    "amount": {"type": "integer", "description": "Delta to apply (can be negative)"},
                },
                "required": ["stat", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_cyberware",
            "description": "Install cyberware on the player. Deducts creds and humanity, adds it to their rig.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the cyberware"},
                    "cost_creds": {"type": "integer"},
                    "cost_humanity": {"type": "integer", "description": "Humanity cost, 5-20"},
                },
                "required": ["name", "cost_creds", "cost_humanity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_location",
            "description": "Move the scene to a new place. Call whenever the player arrives somewhere new.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "enum": LOCATIONS},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mission",
            "description": "Set this run's mission. Call once at the start of a run with ONE concrete, achievable goal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short mission name, e.g. 'Criteria Heist'"},
                    "objective": {"type": "string", "description": "One concrete goal, e.g. 'Steal the judging criteria from the Delta Corp server room'"},
                },
                "required": ["title", "objective"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_rating",
            "description": "Grade the finished run. Only call when told the run is over.",
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "enum": GRADES},
                    "score": {"type": "integer", "description": "0-100"},
                    "verdict": {"type": "string", "description": "2 to 3 sentences"},
                },
                "required": ["grade", "score", "verdict"],
            },
        },
    },
]

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_MISSION = {"title": "Criteria Heist", "objective": "Steal the judging criteria from the Delta Corp server room"}
START_HP, START_HUMANITY, START_CREDS = 20, 100, 500


def new_run_state() -> dict:
    return {"hp": START_HP, "location": "street", "mission": {"title": "", "objective": ""},
            "turn": 0, "max_turns": MAX_TURNS, "rating": None, "opened": False, "humanity_start": None}


api_key = os.environ.get("BACKBOARD_API_KEY", "")
client = BackboardClient(api_key=api_key)

app = FastAPI(title="NEXUS")


# ---------------------------------------------------------------------------
# Time: one run per day in Toronto time (falls back to fixed DST rules without tzdata)
# ---------------------------------------------------------------------------
try:
    TZ = ZoneInfo("America/Toronto")
except Exception:
    TZ = None


def toronto_now() -> datetime:
    if TZ:
        return datetime.now(TZ)
    utc = datetime.now(timezone.utc)

    def sunday(month: int, n: int) -> datetime:  # n-th Sunday of the month
        d = datetime(utc.year, month, 1, tzinfo=timezone.utc)
        return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))

    dst = sunday(3, 2).replace(hour=7) <= utc < sunday(11, 1).replace(hour=6)
    return utc.astimezone(timezone(timedelta(hours=-4 if dst else -5)))


def day_str(offset: int = 0) -> str:
    return (toronto_now().date() + timedelta(days=offset)).isoformat()


def next_reset_seconds() -> int:
    now = toronto_now()
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo)
    return max(1, int((midnight - now).total_seconds()))


# ---------------------------------------------------------------------------
# SQLite persistence: players + runs (JSON columns for lists)
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("NEXUS_DB") or (
    "/tmp/nexus.db" if os.environ.get("VERCEL") else str(Path(__file__).parent / "nexus.db"))
JSON_FIELDS = {"stats": "{}", "cyberware": "[]", "inventory": "[]", "npcs": "[]", "missions": "[]"}


def sql(query: str, args: tuple = ()) -> list:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        with con:
            cur = con.execute(query, args)
            rows = [dict(r) for r in cur.fetchall()]
            sql.lastrowid = cur.lastrowid
            return rows
    finally:
        con.close()


def init_db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS players (
          id TEXT PRIMARY KEY, handle TEXT NOT NULL, class TEXT, stats TEXT DEFAULT '{}',
          humanity INTEGER DEFAULT 100, creds INTEGER DEFAULT 500,
          cyberware TEXT DEFAULT '[]', inventory TEXT DEFAULT '[]', npcs TEXT DEFAULT '[]',
          missions TEXT DEFAULT '[]', streak INTEGER DEFAULT 0, best_score INTEGER DEFAULT 0,
          last_played TEXT, last_seen TEXT, assistant_id TEXT, prompt_ver TEXT,
          teaser TEXT DEFAULT '', created_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS players_handle ON players(lower(handle));
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL, day TEXT NOT NULL,
          thread_id TEXT, state TEXT NOT NULL, started INTEGER DEFAULT 0, finished INTEGER DEFAULT 0,
          score INTEGER, grade TEXT, created_at TEXT);
        CREATE INDEX IF NOT EXISTS runs_player_day ON runs(player_id, day);
        CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, messages INTEGER DEFAULT 0);
        """)
    finally:
        con.close()


init_db()


def load_player(pid: str) -> Optional[dict]:
    rows = sql("SELECT * FROM players WHERE id=?", (pid,))
    if not rows:
        return None
    p = rows[0]
    for k, empty in JSON_FIELDS.items():
        p[k] = json.loads(p[k] or empty)
    return p


def save_player(p: dict):
    cols = [k for k in p if k != "id"]
    vals = [json.dumps(p[k]) if k in JSON_FIELDS else p[k] for k in cols]
    sql(f"UPDATE players SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, p["id"]))


def latest_run(pid: str) -> Optional[dict]:
    """Today's most recent run for this player (runs belong to their Toronto day)."""
    rows = sql("SELECT * FROM runs WHERE player_id=? AND day=? ORDER BY id DESC LIMIT 1", (pid, day_str()))
    if not rows:
        return None
    run = rows[0]
    run["state"] = {**new_run_state(), **json.loads(run["state"])}
    return run


def save_run(run: dict):
    sql("UPDATE runs SET thread_id=?, state=?, started=?, finished=?, score=?, grade=? WHERE id=?",
        (run["thread_id"], json.dumps(run["state"]), run["started"], run["finished"],
         run["score"], run["grade"], run["id"]))


def played_today(pid: str) -> bool:
    return bool(sql("SELECT 1 FROM runs WHERE player_id=? AND day=? AND started=1", (pid, day_str())))


class Ctx:
    """One player's request context: profile row + today's run (no module-level game state)."""
    def __init__(self, p: dict, run: Optional[dict]):
        self.p, self.run = p, run

    @property
    def st(self) -> dict:
        return self.run["state"]

    @property
    def active(self) -> bool:
        return bool(self.run and not self.run["finished"])

    def save(self):
        save_player(self.p)
        if self.run:
            save_run(self.run)


_LOCKS: dict = defaultdict(asyncio.Lock)  # one request at a time per player


def daily_refresh(p: dict):
    """First visit of a new day: Humanity recovers +10 (max 100). HP refills because each run starts fresh."""
    today = day_str()
    if p["last_seen"] != today:
        if p["last_seen"]:
            p["humanity"] = min(100, p["humanity"] + 10)
        p["last_seen"] = today
        save_player(p)


def can_play(ctx: Ctx) -> bool:
    return DEV_UNLIMITED or ctx.active or not played_today(ctx.p["id"])


# ---------------------------------------------------------------------------
# Abuse limits: per-IP sliding window + global daily message cap
# ---------------------------------------------------------------------------
_IP_HITS: dict = defaultdict(deque)


def client_ip(request: Request) -> str:
    if os.environ.get("VERCEL"):  # behind Vercel's proxy the real client is in these headers
        fwd = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if fwd.strip():
            return fwd.strip()
    return request.client.host if request.client else "?"


def ip_allowed(ip: str) -> bool:
    now, hits = time.time(), _IP_HITS[ip]
    while hits and now - hits[0] > IP_WINDOW:
        hits.popleft()
    if len(hits) >= IP_LIMIT:
        return False
    hits.append(now)
    return True


def daily_cap_allowed() -> bool:
    d = day_str()
    sql("INSERT INTO usage(day, messages) VALUES(?, 1) ON CONFLICT(day) DO UPDATE SET messages = messages + 1", (d,))
    return sql("SELECT messages FROM usage WHERE day=?", (d,))[0]["messages"] <= MAX_DAILY_MESSAGES


# ---------------------------------------------------------------------------
# SDK compatibility: filter kwargs to what the installed SDK actually accepts
# ---------------------------------------------------------------------------
def _filter_kwargs(fn, kwargs: dict) -> dict:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    params = sig.parameters.values()
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return kwargs
    names = {p.name for p in params}
    return {k: v for k, v in kwargs.items() if k in names}


async def _call(fn, *args, **kwargs):
    return await fn(*args, **_filter_kwargs(fn, kwargs))


def _get(obj: Any, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None:
                return v
    return default


# ---------------------------------------------------------------------------
# One Backboard assistant per player, created lazily; prompt re-synced when it changes
# ---------------------------------------------------------------------------
def prompt_version() -> str:
    return hashlib.sha1((SYSTEM_PROMPT + json.dumps(TOOLS, sort_keys=True)).encode()).hexdigest()[:12]


async def ensure_assistant(ctx: Ctx) -> str:
    p = ctx.p
    if p["assistant_id"]:
        if p["prompt_ver"] != prompt_version():
            try:
                await _call(client.update_assistant, p["assistant_id"], system_prompt=SYSTEM_PROMPT, tools=TOOLS)
                p["prompt_ver"] = prompt_version()
            except Exception as e:
                print(f"[nexus] prompt sync skipped: {type(e).__name__}")
        return p["assistant_id"]
    assistant = await _call(client.create_assistant, name=f"NEXUS GM {p['id'][:8]}",
                            system_prompt=SYSTEM_PROMPT, tools=TOOLS)
    p["assistant_id"] = str(_get(assistant, "assistant_id", "id"))
    p["prompt_ver"] = prompt_version()
    save_player(p)
    return p["assistant_id"]


# ---------------------------------------------------------------------------
# Game tools (dice come from Python's random, never the LLM; every number is clamped)
# ---------------------------------------------------------------------------
STAT_CLAMP = {"hp": (-10, 8), "humanity": (-20, 10), "creds": (-300, 300)}  # per call


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def execute_tool(ctx: Ctx, name: str, args: dict, events: dict) -> dict:
    st, p = ctx.st, ctx.p
    if name == "roll_d20":
        value = random.randint(1, 20)
        events["rolls"].append(value)
        outcome = (
            "CRITICAL SUCCESS" if value == 20 else
            "CRITICAL FAILURE" if value == 1 else
            "success" if value >= 15 else
            "success at a cost" if value >= 10 else
            "failure"
        )
        return {"roll": value, "outcome": outcome}

    if name == "change_stat":
        stat = args.get("stat")
        if stat not in STAT_CLAMP:
            return {"error": f"unknown stat {stat}"}
        lo, hi = STAT_CLAMP[stat]
        amount = max(lo, min(hi, _int(args.get("amount"))))
        holder = st if stat == "hp" else p
        cap = 100 if stat == "humanity" else 10 ** 9
        holder[stat] = max(0, min(cap, holder[stat] + amount))
        events["stat_changes"].append({"stat": stat, "amount": amount, "new": holder[stat]})
        return {"stat": stat, "changed_by": amount, "new_value": holder[stat]}

    if name == "install_cyberware":
        cname = str(args.get("name", "Unknown Implant")).strip()[:40] or "Unknown Implant"
        cost_creds = max(50, min(800, _int(args.get("cost_creds"), 200)))
        cost_humanity = max(5, min(20, _int(args.get("cost_humanity"), 10)))
        if p["creds"] < cost_creds:
            return {"error": "insufficient creds", "creds": p["creds"]}
        p["creds"] -= cost_creds
        p["humanity"] = max(0, p["humanity"] - cost_humanity)
        p["cyberware"].append(cname)
        events["installed"].append(cname)
        return {"installed": cname, "creds_left": p["creds"], "humanity_left": p["humanity"]}

    if name == "set_location":
        loc = args.get("location")
        if loc not in LOCATIONS:
            return {"error": f"unknown location {loc}", "valid": LOCATIONS}
        st["location"] = loc
        events["locations"].append(loc)
        return {"location": loc}

    if name == "set_mission":
        if st["mission"]["objective"]:
            return {"error": "mission already set for this run", "mission": st["mission"]}
        objective = str(args.get("objective", "")).strip()[:160]
        if not objective:
            return {"error": "objective is required"}
        st["mission"] = {"title": str(args.get("title", "")).strip()[:48] or "Untitled Run", "objective": objective}
        return {"mission": st["mission"]}

    if name == "submit_rating":
        if not events.get("judging"):
            return {"error": "the run is not over yet; keep playing"}
        grade = str(args.get("grade", "")).strip().upper()
        if grade not in GRADES:
            return {"error": f"grade must be one of {GRADES}"}
        score = max(0, min(100, _int(args.get("score"))))
        st["rating"] = {"grade": grade, "score": score, "verdict": str(args.get("verdict", "")).strip()[:600]}
        return {"stored": True}

    return {"error": f"unknown tool {name}"}


def run_end_reason(ctx: Ctx) -> Optional[str]:
    st = ctx.st
    if st["hp"] <= 0:
        return "flatlined"
    if ctx.p["humanity"] <= 0:
        return "cyberpsychosis"
    if st["mission"]["objective"] and st["turn"] >= st["max_turns"]:
        return "out of turns"
    return None


def finish_run(ctx: Ctx, reason: str):
    """Close the run: creds by grade, mission history, best score, cyberpsychosis penalties."""
    st, p = ctx.st, ctx.p
    r = st["rating"]
    r["reason"] = reason
    if reason == "cyberpsychosis":  # chrome burns out: lose all cyberware, reset humanity, half the creds
        p["cyberware"] = []
        p["humanity"] = 40
        p["creds"] //= 2
    earned = CREDS_BY_GRADE.get(r["grade"], 0)
    p["creds"] += earned
    r["creds_earned"] = earned
    r["humanity_change"] = p["humanity"] - (st["humanity_start"] if st["humanity_start"] is not None else p["humanity"])
    p["missions"] = (p["missions"] + [{"date": ctx.run["day"], "title": st["mission"]["title"],
                                       "grade": r["grade"], "score": r["score"], "creds": earned}])[-30:]
    p["best_score"] = max(p["best_score"] or 0, r["score"])
    ctx.run.update(finished=1, score=r["score"], grade=r["grade"])


def mark_started(ctx: Ctx):
    """The first typed prompt is what counts as today's play; also advances the streak."""
    if ctx.run["started"]:
        return
    ctx.run["started"] = 1
    ctx.st["humanity_start"] = ctx.p["humanity"]
    p, today = ctx.p, day_str()
    if p["last_played"] != today:
        p["streak"] = (p["streak"] or 0) + 1 if p["last_played"] == day_str(-1) else 1
        p["last_played"] = today


def view_state(ctx: Ctx) -> dict:
    p = ctx.p
    st = ctx.st if ctx.run else new_run_state()
    return {"hp": st["hp"], "humanity": p["humanity"], "creds": p["creds"], "cyberware": p["cyberware"],
            "inventory": p["inventory"], "location": st["location"], "handle": p["handle"],
            "class": p["class"], "stats": p["stats"]}


def run_fields(ctx: Ctx) -> dict:
    st = ctx.st if ctx.run else new_run_state()
    return {
        "mission": st["mission"],
        "turn": st["turn"],
        "max_turns": st["max_turns"],
        "rating": st["rating"],
        "opened": st["opened"],
        "run_over": bool(ctx.run and ctx.run["finished"]),
    }


def status_fields(ctx: Ctx) -> dict:
    p = ctx.p
    return {
        "can_play": can_play(ctx),
        "active": ctx.active,
        "next_reset_seconds": next_reset_seconds(),
        "dev_unlimited": DEV_UNLIMITED,
        "streak": p["streak"] or 0,
        "best_score": p["best_score"] or 0,
        "history": p["missions"][-5:],
    }


QUOTA_HINTS = ("credit", "quota", "insufficient", "billing", "payment", "rate limit",
               "api key", "unauthorized", "forbidden", "authentication")


def is_quota_or_auth_error(e: Exception) -> bool:
    if getattr(e, "status_code", None) in (401, 402, 403, 429):
        return True
    return any(h in str(e).lower() for h in QUOTA_HINTS)


# ---------------------------------------------------------------------------
# Memory helpers (scoped to the player's own assistant)
# ---------------------------------------------------------------------------
def _normalize_memories(resp: Any) -> list:
    items = _get(resp, "memories", default=None)
    if items is None:
        items = resp if isinstance(resp, list) else []
    out = []
    for m in items:
        mid = _get(m, "id", "memory_id")
        content = _get(m, "content", "memory", "text", default="")
        if mid:
            out.append({"id": str(mid), "content": str(content)})
    return out


async def list_memories(ctx: Ctx) -> list:
    if not ctx.p["assistant_id"]:
        return []
    try:
        resp = await _call(client.get_memories, ctx.p["assistant_id"])
    except Exception:
        return []
    return _normalize_memories(resp)


async def apply_cyberpsychosis(ctx: Ctx) -> list:
    """Below 50 humanity: delete 1 random memory per turn. Below 25: delete 2."""
    hum = ctx.p["humanity"]
    n = 2 if hum < 25 else 1 if hum < 50 else 0
    if n == 0 or not ctx.p["assistant_id"]:
        return []
    memories = await list_memories(ctx)
    if not memories:
        return []
    victims = random.sample(memories, min(n, len(memories)))
    deleted = []
    for v in victims:
        try:
            await _call(client.delete_memory, ctx.p["assistant_id"], v["id"])
            deleted.append(v)
        except Exception:
            pass
    return deleted


# ---------------------------------------------------------------------------
# Chat round-trip with tool loop (non-streaming; frontend fakes typewriter)
# ---------------------------------------------------------------------------
async def _exchange(ctx: Ctx, content: str, kwargs: dict, events: dict) -> Any:
    """Send one message, then run the tool loop until the GM stops calling tools."""
    resp = await _call(client.send_message, content, **kwargs)
    tid = _get(resp, "thread_id")
    if tid:
        ctx.run["thread_id"] = kwargs["thread_id"] = str(tid)

    for _ in range(8):
        status = str(_get(resp, "status", default="")).upper()
        tool_calls = _get(resp, "tool_calls", default=None)
        if status != "REQUIRES_ACTION" or not tool_calls:
            break
        outputs = []
        for tc in tool_calls:
            fn = _get(tc, "function")
            name = _get(fn, "name", default="")
            args = _get(fn, "parsed_arguments", default=None)
            if args is None:
                raw = _get(fn, "arguments", default="{}")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except json.JSONDecodeError:
                    args = {}
            result = execute_tool(ctx, name, args if isinstance(args, dict) else {}, events)
            outputs.append({"tool_call_id": _get(tc, "id"), "output": json.dumps(result)})
        resp = await _call(
            client.submit_tool_outputs_simple,
            thread_id=ctx.run["thread_id"],
            tool_outputs=outputs,
        )
    return resp


def player_sheet(ctx: Ctx) -> str:
    p = ctx.p
    stats = ", ".join(f"{k} {v}" for k, v in p["stats"].items())
    return (f"Player: {p['handle']}, class {CLASSES.get(p['class'], {}).get('name', '?')} ({stats}). "
            f"HP {ctx.st['hp']}, Humanity {p['humanity']}, creds {p['creds']}, "
            f"cyberware: {', '.join(p['cyberware']) or 'none'}.")


def opening_message(ctx: Ctx, kind: str) -> str:
    """Hidden boot messages are written by the server, never by the client."""
    if kind == "resume":
        return ("A new session begins mid-mission. Open with a short 'Previously in Neo-Hamilton...' recap of "
                "this mission so far, then set the scene and give me a choice.")
    if ctx.p["missions"]:
        return ("A new day, a new run. Briefly nod to what you remember about me from earlier runs, then brief me "
                "on the new mission and set the opening scene. End with a clear choice.")
    return ("I am a new runner jacking in for the first time. Introduce the world of Neo-Hamilton 2077, brief me "
            "on my mission and set the opening scene. End with a clear choice.")


def pacing_note(ctx: Ctx) -> str:
    st = ctx.st
    if not st["mission"]["objective"]:
        kind = random.choice(["heist", "rescue", "sabotage", "escape", "delivery"])  # nudge variety across runs
        return ("[system: start a new run. Call set_mission with one specific goal, then set the opening scene. "
                f"This run is a {kind} mission, different from any earlier run. {player_sheet(ctx)}]")
    t, n = st["turn"], st["max_turns"]
    sheet = player_sheet(ctx)
    if t >= n:
        return (f"[system: turn {n} of {n}. FINAL TURN. Resolve the mission NOW as a success, partial success "
                "or failure based on the player's actions, rolls and stats. Write a definitive ending in under "
                f"80 words. Do NOT ask what the player does next, do NOT offer choices, do NOT set up a sequel. {sheet}]")
    if t == n - 1:
        return (f"[system: turn {t} of {n}. CLIMAX. This is the decisive moment. The next prompt is the last. "
                f"End with a choice that decides the outcome. {sheet}]")
    if t <= 1:
        return f"[system: turn {max(t, 1)} of {n}. SETUP. Establish the stakes and the first obstacle. End with a choice. {sheet}]"
    return (f"[system: turn {t} of {n}. COMPLICATION. Raise the stakes or add a twist. "
            f"Move toward the climax. End with a choice. {sheet}]")


async def run_turn(ctx: Ctx, message: str, mode: str, kind: str) -> dict:
    aid = await ensure_assistant(ctx)
    model_cfg = MODE_MODELS.get(mode, MODE_MODELS[DEFAULT_MODE])
    events = {"rolls": [], "stat_changes": [], "installed": [], "locations": [], "judging": False}
    kwargs = dict(assistant_id=aid, memory="Auto", tools=TOOLS, **model_cfg)
    if ctx.run["thread_id"]:
        kwargs["thread_id"] = ctx.run["thread_id"]

    st = ctx.st
    counted = kind == "turn"
    if counted:
        st["turn"] += 1
    else:
        message = opening_message(ctx, kind)
    resp = await _exchange(ctx, f"{message}\n\n{pacing_note(ctx)}", kwargs, events)  # on error nothing is saved
    if counted:
        mark_started(ctx)
    st["opened"] = True
    text = _get(resp, "content", default="") or "[signal lost — the net flickers]"
    if not st["mission"]["objective"]:
        st["mission"] = dict(DEFAULT_MISSION)  # GM skipped set_mission; keep the run playable
    deleted = await apply_cyberpsychosis(ctx)

    # Run over? One extra hidden call so the GM grades the run.
    reason = run_end_reason(ctx) if counted else None
    if reason and st["rating"] is None:
        events["judging"] = True
        try:
            await _exchange(
                ctx,
                f"[system: the run is over (reason: {reason}). Do NOT continue the story. Only call "
                "submit_rating, judging the player's actual choices, rolls and stats.]",
                kwargs, events,
            )
        except Exception:
            pass
        if st["rating"] is None:
            st["rating"] = {"grade": "D", "score": 40, "verdict": "The GM could not be reached for a verdict."}
        finish_run(ctx, reason)
    ctx.save()

    return {
        "text": text,
        "state": view_state(ctx),
        "rolls": events["rolls"],
        "stat_changes": events["stat_changes"],
        "installed": events["installed"],
        "location": st["location"],
        "locations": events["locations"],
        "deleted_memories": deleted,
        **run_fields(ctx),
        **status_fields(ctx),
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
PID_COOKIE = "nexus_pid"


@app.middleware("http")
async def player_cookie(request: Request, call_next):
    """Every browser gets a random player id (httponly, 1 year); all state is keyed by it."""
    raw = request.cookies.get(PID_COOKIE, "")
    try:
        pid = str(uuid.UUID(raw))
    except ValueError:
        pid = ""
    new = not pid
    request.state.pid = pid or str(uuid.uuid4())
    response = await call_next(request)
    if new:
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(PID_COOKIE, request.state.pid, max_age=365 * 24 * 3600,
                            httponly=True, samesite="lax", secure=https)
    return response


def err(status: int, msg: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg, **extra})


class MessageIn(BaseModel):
    message: str = ""
    mode: str = DEFAULT_MODE
    kind: str = "turn"  # turn (typed by the player) | open | resume (hidden, text built server-side)


class ProfileIn(BaseModel):
    handle: str
    cls: str
    adult: bool = False


HANDLE_RE = re.compile(r"^[A-Za-z0-9_ ]{3,16}$")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state(request: Request):
    pid = request.state.pid
    async with _LOCKS[pid]:
        p = load_player(pid)
        if not p:
            return {"needs_profile": True, "classes": CLASSES}
        daily_refresh(p)
        ctx = Ctx(p, latest_run(pid))
        mems = await list_memories(ctx)
        return {
            "state": view_state(ctx),
            "memories": mems,
            "has_memories": bool(mems),
            "classes": CLASSES,
            **run_fields(ctx),
            **status_fields(ctx),
        }


@app.post("/api/profile")
async def create_profile(body: ProfileIn, request: Request):
    pid = request.state.pid
    handle = " ".join(body.handle.split())
    if not body.adult:
        return err(400, "You must confirm you are 18 or older to play.")
    if not HANDLE_RE.match(handle):
        return err(400, "Handle: 3 to 16 letters, numbers, underscores or spaces.")
    if body.cls not in CLASSES:
        return err(400, "Pick a class.")
    async with _LOCKS[pid]:
        if load_player(pid):
            return {"ok": True}
        if sql("SELECT 1 FROM players WHERE lower(handle)=lower(?)", (handle,)):
            return err(409, "That handle is taken. Pick another.")
        sql("INSERT INTO players(id, handle, class, stats, humanity, creds, last_seen, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (pid, handle, body.cls, json.dumps(CLASSES[body.cls]["stats"]), START_HUMANITY, START_CREDS,
             day_str(), datetime.now(timezone.utc).isoformat()))
    return {"ok": True}


@app.post("/api/run/start")
async def start_run(request: Request):
    pid = request.state.pid
    async with _LOCKS[pid]:
        p = load_player(pid)
        if not p:
            return err(400, "Create a runner first.")
        daily_refresh(p)
        ctx = Ctx(p, latest_run(pid))
        if ctx.active:
            return {"ok": True}
        if not can_play(ctx):
            return err(403, "You already ran today. Come back tomorrow.", next_reset_seconds=next_reset_seconds())
        sql("INSERT INTO runs(player_id, day, state, created_at) VALUES(?,?,?,?)",
            (pid, day_str(), json.dumps(new_run_state()), datetime.now(timezone.utc).isoformat()))
    return {"ok": True}


@app.get("/api/memories")
async def get_memories_ep(request: Request):
    p = load_player(request.state.pid)
    return {"memories": await list_memories(Ctx(p, None)) if p else []}


@app.post("/api/message")
async def post_message(body: MessageIn, request: Request):
    if not api_key or api_key == "PUT_YOUR_KEY_HERE":
        return err(500, "BACKBOARD_API_KEY missing: put it in .env")
    kind = body.kind if body.kind in ("turn", "open", "resume") else "turn"
    text = body.message.strip()
    if kind == "turn" and not PROMPT_MIN <= len(text) <= PROMPT_MAX:
        return err(400, f"Prompts must be {PROMPT_MIN} to {PROMPT_MAX} characters.")
    if not ip_allowed(client_ip(request)):
        return err(429, "Easy, choom. 20 messages per 10 minutes. Breathe, then try again.")
    if not daily_cap_allowed():
        return err(503, "NEXUS is overloaded, come back tomorrow.")
    pid = request.state.pid
    async with _LOCKS[pid]:
        p = load_player(pid)
        if not p:
            return err(400, "Create a runner first.")
        ctx = Ctx(p, latest_run(pid))
        if not ctx.active:
            return err(409, "run over")
        st = ctx.st
        if kind == "turn" and (st["rating"] is not None or run_end_reason(ctx) or not st["opened"]):
            return err(409, "run over")  # hard stop: no LLM call
        if kind == "open" and st["opened"]:
            kind = "resume"
        try:
            return await run_turn(ctx, text, body.mode, kind)
        except Exception as e:  # surface errors to the terminal UI
            msg = f"{type(e).__name__}: {e}"
            if is_quota_or_auth_error(e):
                msg = "NETWORK QUOTA EXCEEDED: " + msg
            return err(500, msg)


@app.post("/api/new_session")
async def new_session(request: Request):
    pid = request.state.pid
    async with _LOCKS[pid]:
        run = latest_run(pid)
        if run and not run["finished"]:
            run["thread_id"] = None
            save_run(run)
    return {"ok": True}


@app.post("/api/new_campaign")
async def new_campaign(request: Request):
    """Wipe this runner: fresh assistant (zero memories), fresh gear. Today's limit still applies."""
    pid = request.state.pid
    async with _LOCKS[pid]:
        p = load_player(pid)
        if not p:
            return err(400, "Create a runner first.")
        run = latest_run(pid)
        if run and run["started"] and not run["finished"]:
            return err(409, "Finish today's run first.")
        if p["assistant_id"]:
            try:
                await _call(client.delete_assistant, p["assistant_id"])
            except Exception:
                pass
        p.update(humanity=START_HUMANITY, creds=START_CREDS, cyberware=[], inventory=[], npcs=[],
                 missions=[], assistant_id=None, prompt_ver=None, teaser="")
        save_player(p)
        if run and not run["started"]:
            sql("DELETE FROM runs WHERE id=?", (run["id"],))
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
