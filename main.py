"""NEXUS — cyberpunk tabletop RPG with a Backboard-powered GM that remembers everything."""
import inspect
import json
import os
import random
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backboard import BackboardClient

load_dotenv()

# ---------------------------------------------------------------------------
# EDIT ME: per-mode model routing (llm_provider + model_name sent per message)
# ---------------------------------------------------------------------------
MODE_MODELS = {
    "story":  {"llm_provider": "openai", "model_name": "gpt-4o-mini"},
    "combat": {"llm_provider": "openai", "model_name": "gpt-4o-mini"},
    "netrun": {"llm_provider": "openai", "model_name": "gpt-4o-mini"},
}
DEFAULT_MODE = "story"

SYSTEM_PROMPT = (
    "You are NEXUS, the game master of a cyberpunk tabletop RPG set in Neo-Hamilton, 2077, "
    "a megacity built over dead steel mills. The ruling megacorp is Delta Corp, whose logo is "
    "an impossible triangle. The player has HP, Humanity, and creds. Whenever an action is risky, you MUST "
    "call the roll_d20 tool yourself before narrating the result; never ask the player to "
    "roll. 15+ succeeds, 10-14 succeeds at a cost, under 10 fails, natural 20 and 1 are "
    "dramatic. Cyberware costs creds and 5-20 Humanity; use install_cyberware. Below 50 "
    "Humanity your narration becomes glitchy and paranoid; below 25 you are hostile. Remember "
    "the player's choices, allies, enemies, and debts and bring them back later. Keep replies "
    "under 120 words, second person, ending with a clear choice. Stay in character. "
    "Call set_location whenever the scene moves to a new place. "
    "Every run is a mission. At the start of every run, call set_mission with ONE concrete, "
    "achievable goal that fits Neo-Hamilton 2077, and vary it each run (heist, rescue, sabotage, "
    "escape, delivery). Keep every reply moving the player toward or away from that goal. Hidden "
    "[system: ...] notes give you the turn count: never quote them or lecture about the turn limit, "
    "but warn the player naturally in the story when 1 turn remains. When told the run is over, "
    "call submit_rating and be honest: S only for a clever, fully achieved goal; D and F for clear "
    "failure. In the verdict and your final lines, mention the player's earlier choices and what "
    "the world will remember."
)

LOCATIONS = ["alley", "street", "corp_lobby", "clinic", "rooftop", "server_room"]
GRADES = ["S", "A", "B", "C", "D", "F"]

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

ASSISTANT_FILE = Path(__file__).parent / "nexus_assistant.json"
STATIC_DIR = Path(__file__).parent / "static"

INITIAL_STATE = {"hp": 20, "humanity": 100, "creds": 500, "cyberware": [], "location": "street",
                 "mission": {"title": "", "objective": ""}, "turn": 0, "max_turns": 5, "rating": None}
DEFAULT_MISSION = {"title": "Criteria Heist", "objective": "Steal the judging criteria from the Delta Corp server room"}


def fresh_state() -> dict:
    return {**INITIAL_STATE, "cyberware": [], "mission": {"title": "", "objective": ""}}


GAME_STATE: dict = fresh_state()
SESSION: dict = {"thread_id": None, "assistant_id": None}

api_key = os.environ.get("BACKBOARD_API_KEY", "")
client = BackboardClient(api_key=api_key)

app = FastAPI(title="NEXUS")


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
# Assistant bootstrap (created once, id cached on disk)
# ---------------------------------------------------------------------------
async def ensure_assistant() -> str:
    if SESSION["assistant_id"]:
        return SESSION["assistant_id"]
    if ASSISTANT_FILE.exists():
        data = json.loads(ASSISTANT_FILE.read_text())
        SESSION["assistant_id"] = str(data["assistant_id"])
        return SESSION["assistant_id"]
    assistant = await _call(
        client.create_assistant,
        name="NEXUS GM",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
    aid = str(_get(assistant, "assistant_id", "id"))
    ASSISTANT_FILE.write_text(json.dumps({"assistant_id": aid}))
    SESSION["assistant_id"] = aid
    return aid


# ---------------------------------------------------------------------------
# Game tools (dice come from Python's random, never the LLM)
# ---------------------------------------------------------------------------
def execute_tool(name: str, args: dict, events: dict) -> dict:
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
        amount = int(args.get("amount", 0))
        if stat not in ("hp", "humanity", "creds"):
            return {"error": f"unknown stat {stat}"}
        GAME_STATE[stat] += amount
        if stat == "humanity":
            GAME_STATE[stat] = max(0, min(100, GAME_STATE[stat]))
        else:
            GAME_STATE[stat] = max(0, GAME_STATE[stat])
        events["stat_changes"].append({"stat": stat, "amount": amount, "new": GAME_STATE[stat]})
        return {"stat": stat, "changed_by": amount, "new_value": GAME_STATE[stat]}

    if name == "install_cyberware":
        cname = str(args.get("name", "Unknown Implant"))
        cost_creds = int(args.get("cost_creds", 0))
        cost_humanity = int(args.get("cost_humanity", 10))
        if GAME_STATE["creds"] < cost_creds:
            return {"error": "insufficient creds", "creds": GAME_STATE["creds"]}
        GAME_STATE["creds"] -= cost_creds
        GAME_STATE["humanity"] = max(0, GAME_STATE["humanity"] - cost_humanity)
        GAME_STATE["cyberware"].append(cname)
        events["installed"].append(cname)
        return {
            "installed": cname,
            "creds_left": GAME_STATE["creds"],
            "humanity_left": GAME_STATE["humanity"],
        }

    if name == "set_location":
        loc = args.get("location")
        if loc not in LOCATIONS:
            return {"error": f"unknown location {loc}", "valid": LOCATIONS}
        GAME_STATE["location"] = loc
        events["locations"].append(loc)
        return {"location": loc}

    if name == "set_mission":
        if GAME_STATE["mission"]["objective"]:
            return {"error": "mission already set for this run", "mission": GAME_STATE["mission"]}
        objective = str(args.get("objective", "")).strip()
        if not objective:
            return {"error": "objective is required"}
        GAME_STATE["mission"] = {"title": str(args.get("title", "")).strip() or "Untitled Run", "objective": objective}
        return {"mission": GAME_STATE["mission"]}

    if name == "submit_rating":
        if not events.get("judging"):
            return {"error": "the run is not over yet; keep playing"}
        grade = str(args.get("grade", "")).strip().upper()
        if grade not in GRADES:
            return {"error": f"grade must be one of {GRADES}"}
        try:
            score = max(0, min(100, int(args.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        GAME_STATE["rating"] = {"grade": grade, "score": score, "verdict": str(args.get("verdict", "")).strip()}
        return {"stored": True}

    return {"error": f"unknown tool {name}"}


def run_end_reason() -> Optional[str]:
    if GAME_STATE["hp"] <= 0:
        return "flatlined"
    if GAME_STATE["humanity"] <= 0:
        return "cyberpsychosis"
    if GAME_STATE["mission"]["objective"] and GAME_STATE["turn"] >= GAME_STATE["max_turns"]:
        return "out of turns"
    return None


def run_fields() -> dict:
    return {
        "mission": GAME_STATE["mission"],
        "turn": GAME_STATE["turn"],
        "max_turns": GAME_STATE["max_turns"],
        "rating": GAME_STATE["rating"],
        "run_over": GAME_STATE["rating"] is not None,
    }


QUOTA_HINTS = ("credit", "quota", "insufficient", "billing", "payment", "rate limit",
               "api key", "unauthorized", "forbidden", "authentication")


def is_quota_or_auth_error(e: Exception) -> bool:
    if getattr(e, "status_code", None) in (401, 402, 403, 429):
        return True
    return any(h in str(e).lower() for h in QUOTA_HINTS)


# ---------------------------------------------------------------------------
# Memory helpers
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


async def list_memories() -> list:
    try:
        aid = await ensure_assistant()
        resp = await _call(client.get_memories, aid)
    except Exception:
        return []
    return _normalize_memories(resp)


async def apply_cyberpsychosis() -> list:
    """Below 50 humanity: delete 1 random memory per turn. Below 25: delete 2."""
    hum = GAME_STATE["humanity"]
    n = 2 if hum < 25 else 1 if hum < 50 else 0
    if n == 0:
        return []
    aid = await ensure_assistant()
    memories = await list_memories()
    if not memories:
        return []
    victims = random.sample(memories, min(n, len(memories)))
    deleted = []
    for v in victims:
        try:
            await _call(client.delete_memory, aid, v["id"])
            deleted.append(v)
        except Exception:
            pass
    return deleted


# ---------------------------------------------------------------------------
# Chat round-trip with tool loop (non-streaming; frontend fakes typewriter)
# ---------------------------------------------------------------------------
async def _exchange(content: str, kwargs: dict, events: dict) -> Any:
    """Send one message, then run the tool loop until the GM stops calling tools."""
    resp = await _call(client.send_message, content, **kwargs)
    tid = _get(resp, "thread_id")
    if tid:
        SESSION["thread_id"] = kwargs["thread_id"] = tid

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
            result = execute_tool(name, args or {}, events)
            outputs.append({"tool_call_id": _get(tc, "id"), "output": json.dumps(result)})
        resp = await _call(
            client.submit_tool_outputs_simple,
            thread_id=SESSION["thread_id"],
            tool_outputs=outputs,
        )
    return resp


async def run_turn(message: str, mode: str, counts_turn: bool = True) -> dict:
    aid = await ensure_assistant()
    model_cfg = MODE_MODELS.get(mode, MODE_MODELS[DEFAULT_MODE])
    events = {"rolls": [], "stat_changes": [], "installed": [], "locations": [], "judging": False}

    kwargs = dict(
        assistant_id=aid,
        memory="Auto",
        tools=TOOLS,
        **model_cfg,
    )
    if SESSION["thread_id"]:
        kwargs["thread_id"] = SESSION["thread_id"]

    # Hidden pacing note: sent to the model, never returned to the frontend
    mission = GAME_STATE["mission"]
    counted = bool(counts_turn and mission["objective"])
    if counted:
        GAME_STATE["turn"] += 1
    if mission["objective"]:
        note = (
            f"[system: turn {GAME_STATE['turn']} of {GAME_STATE['max_turns']}. Mission: {mission['objective']}. "
            "Pace the story so the goal is reachable but not guaranteed. "
            f"Turn {GAME_STATE['max_turns']} is the final turn.]"
        )
    else:
        kind = random.choice(["heist", "rescue", "sabotage", "escape", "delivery"])  # nudge variety across runs
        note = ("[system: start a new run. Call set_mission with one specific goal, then set the opening scene. "
                f"This run is a {kind} mission, different from any earlier run.]")

    try:
        resp = await _exchange(f"{message}\n\n{note}", kwargs, events)
    except Exception:
        if counted:
            GAME_STATE["turn"] -= 1  # a failed call doesn't burn a prompt
        raise
    text = _get(resp, "content", default="") or "[signal lost — the net flickers]"
    if not GAME_STATE["mission"]["objective"]:
        GAME_STATE["mission"] = dict(DEFAULT_MISSION)  # GM skipped set_mission; keep the run playable
    deleted = await apply_cyberpsychosis()

    # Run over? One extra hidden call so the GM grades the run.
    final_text = ""
    reason = run_end_reason()
    if reason and GAME_STATE["rating"] is None:
        events["judging"] = True
        try:
            judge = await _exchange(
                f"[system: the run is over (reason: {reason}). Judge how well the player achieved the "
                "mission, using their actual choices, rolls and stats. Call submit_rating now.]",
                kwargs, events,
            )
            final_text = _get(judge, "content", default="") or ""
        except Exception:
            pass
        if GAME_STATE["rating"] is None:
            GAME_STATE["rating"] = {"grade": "D", "score": 40, "verdict": "The GM could not be reached for a verdict."}
        GAME_STATE["rating"]["reason"] = reason

    return {
        "text": text,
        "final_text": final_text,
        "state": GAME_STATE,
        "rolls": events["rolls"],
        "stat_changes": events["stat_changes"],
        "installed": events["installed"],
        "location": GAME_STATE["location"],
        "locations": events["locations"],
        "deleted_memories": deleted,
        "thread_id": SESSION["thread_id"],
        **run_fields(),
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class MessageIn(BaseModel):
    message: str
    mode: str = DEFAULT_MODE
    counts_turn: bool = True


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    mems = await list_memories()
    return {
        "state": GAME_STATE,
        "has_memories": len(mems) > 0,
        "memories": mems,
        "location": GAME_STATE["location"],
        **run_fields(),
    }


@app.get("/api/memories")
async def get_memories_ep():
    return {"memories": await list_memories()}


@app.post("/api/message")
async def post_message(body: MessageIn):
    if not api_key or api_key == "PUT_YOUR_KEY_HERE":
        return JSONResponse(status_code=500, content={"error": "BACKBOARD_API_KEY missing: put it in .env"})
    if body.counts_turn and GAME_STATE["rating"] is not None:
        return JSONResponse(status_code=409, content={"error": "RUN OVER: press PLAY AGAIN to start a new run"})
    try:
        return await run_turn(body.message, body.mode, body.counts_turn)
    except Exception as e:  # surface errors to the terminal UI
        err = f"{type(e).__name__}: {e}"
        if is_quota_or_auth_error(e):
            err = "NETWORK QUOTA EXCEEDED: " + err
        return JSONResponse(status_code=500, content={"error": err})


@app.post("/api/new_session")
async def new_session():
    SESSION["thread_id"] = None
    return {"ok": True, "state": GAME_STATE}


@app.post("/api/new_campaign")
async def new_campaign():
    """Forget the cached assistant so the next call creates a fresh one with zero memories."""
    ASSISTANT_FILE.unlink(missing_ok=True)
    SESSION.update({"thread_id": None, "assistant_id": None})
    GAME_STATE.update(fresh_state())
    return {"ok": True, "state": GAME_STATE}


@app.post("/api/reset")
async def reset_game():
    """New run: fresh state + thread, same assistant, so Backboard memories (NPCs) carry over."""
    SESSION["thread_id"] = None
    GAME_STATE.update(fresh_state())
    return {"ok": True, "state": GAME_STATE}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
