# NEXUS — Neo-Hamilton 2077

A cyberpunk tabletop RPG run by an AI game master that **remembers everything across
sessions**, powered by the [Backboard.io](https://backboard.io) API (assistant-level
memory, tool calling, per-message model routing).

## Run it

Copy `.env.example` to `.env` and set `BACKBOARD_API_KEY`, then:

```bash
pip install -r requirements.txt
uvicorn main:app --port 8077
```

Open http://localhost:8077

## How Backboard is used

| Feature | Where |
|---|---|
| One assistant, created once, id cached in `nexus_assistant.json` | `ensure_assistant()` in `main.py` |
| Every "session" = new thread on the same assistant, `memory="Auto"` | `run_turn()` / `POST /api/new_session` |
| Tools `roll_d20`, `change_stat`, `install_cyberware` executed in Python, returned via `submit_tool_outputs_simple` | tool loop in `run_turn()` — dice from Python's `random` |
| Neural log sidebar = `get_memories(assistant_id)` | `GET /api/memories` |
| Cyberpsychosis: Humanity < 50 → `delete_memory` 1 random engram per turn, < 25 → 2 | `apply_cyberpsychosis()` — deleted memories glitch out of the sidebar |
| Modes dropdown switches `llm_provider` + `model_name` per message | `MODE_MODELS` dict at top of `main.py` — edit freely |

## 60-second demo script (memory across two sessions)

1. Load the page — fresh assistant, GM sets the opening scene.
2. Type: `My name is Vex. I owe 2000 creds to a fixer called Mireille. I never use guns.`
3. Play one beat; try `I sneak past the Delta Corp checkpoint` → watch the **d20 overlay** roll (server-side `random`).
4. Buy chrome: `Install the cheapest optic implant you have` → creds/Humanity drop in the HUD, implant appears.
5. Watch the **Neural Log** sidebar fill with engrams (real Backboard memories).
6. Click **NEW SESSION** → new thread, same assistant → the GM opens with
   *"Previously in Neo-Hamilton…"* and recalls Vex, the debt to Mireille, no-guns.
7. (Optional chaos) Keep installing cyberware until Humanity < 50 → memories start
   **glitching out of the sidebar** (actual `delete_memory` calls). Below 25 it's two per
   turn; at 0 you get the CYBERPSYCHOSIS game-over screen.

## Known limitations

- Replies are non-streaming; the typewriter effect is client-side (allowed fallback —
  streaming + the tool-call loop was the risky path).
- Game state is in-memory, single player; restart the server to wipe HP/creds (the
  assistant's memories persist on Backboard regardless).
