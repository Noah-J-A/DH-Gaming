# NEXUS — Neo-Hamilton 2077

A daily cyberpunk RPG run by an AI game master that **remembers each player across days**,
powered by the [Backboard.io](https://backboard.io) API (per-player assistant memory, tool
calling, per-message model routing). One mission a day, the same for every runner, five
prompts, scored **80% on how well you write and 20% on the dice**.

## Run it

Copy `.env.example` to `.env` and set `BACKBOARD_API_KEY`, then:

```bash
pip install -r requirements.txt
uvicorn main:app --port 8077
```

Open http://localhost:8077. To test without the one-run-per-day limit:

```bash
DEV_UNLIMITED=1 uvicorn main:app --port 8077
```

(PowerShell: `$env:DEV_UNLIMITED="1"; uvicorn main:app --port 8077`)

**Friends' pass:** share `http://<your-host>/unlimited`. Opening it once sets a cookie that lifts the daily limit
for that browser (1 year). Everyone else still gets one run a day. Set `UNLIMITED_SLUG` to use a harder-to-guess path.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `BACKBOARD_API_KEY` | (required) | Backboard API key. Never commit it. |
| `DEV_UNLIMITED` | off | `1` lifts the one-run-per-day limit, for testing |
| `UNLIMITED_SLUG` | `unlimited` | Path of the friends' pass link that lifts the daily limit per browser |
| `MAX_DAILY_MESSAGES` | `3000` | Global message cap for all players per Toronto day, then "NEXUS is overloaded, come back tomorrow" |
| `NEXUS_DB` | `nexus.db` (`/tmp/nexus.db` on Vercel) | SQLite file for players and runs |
| `MODEL_PROVIDER` / `MODEL_NAME` | `anthropic` / `claude-haiku-5-5` | Override the model for every mode |

## How a day works

1. **First visit:** 18+ notice, a handle and a class (BREAKER, SPLICER, GHOST, HUSTLER), each with stats and a starter item. The server sets an httponly `nexus_pid` cookie; everything is keyed to it in SQLite.
2. **Black market:** 6 daily items for creds. Inventory holds 6.
3. **JACK IN:** today's mission seed from `missions.py` (14 seeds, rotating by day of year). The GM retells it with your own NPCs and gear and sets a **THIS TURN** objective, sometimes with a constraint.
4. **Each prompt:** the GM calls `rate_action` (specificity, creativity, fit, a quote, up to 3 +/- lines). Python computes `action = 3S + 4C + 3F`, rolls the d20, `power = 0.8 × action + 0.2 × d20 × 5` (+ up to 5 for a class stat), picks the tier and the GM must narrate it. A score card shows the breakdown, combo and turn grade.
5. **End:** sum of turn scores + mission bonus (success 100, partial 50) out of 600. Python computes the grade; the GM only writes the verdict. Creds by grade, plus streak bonuses at 3, 7 and 14 days.
6. **Come back:** countdown to Toronto midnight, today's result, streak, the GM's teaser for tomorrow and the leaderboard. Tomorrow opens with a "Previously..." recap from memory.

## How Backboard is used

| Feature | Where |
|---|---|
| One assistant per player, created lazily, id stored in SQLite; prompt and tools re-synced when they change | `ensure_assistant()` |
| Every daily run is a new thread on the player's assistant, `memory="Auto"` | `run_turn()` |
| Tools run in Python and return via `submit_tool_outputs_simple`: `rate_action`, `set_objective`, `use_item`, `grant_item`, `meet_npc`, `update_npc`, `change_stat`, `install_cyberware`, `set_location`, `set_teaser`, `submit_rating` | `execute_tool()` |
| Neural log sidebar = `get_memories` on the player's assistant | `GET /api/memories` |
| Cyberpsychosis: Humanity < 50 deletes 1 memory per turn, < 25 deletes 2; at 0 the run ends, cyberware is lost, Humanity resets to 40 and creds are halved | `apply_cyberpsychosis()`, `finish_run()` |
| Mode dropdown switches `llm_provider` + `model_name` per message | `MODE_MODELS` at the top of `main.py` |

## Fair play

- The server owns every number: the LLM only narrates and proposes ratings, and Python clamps everything.
- Hidden opening and recap messages are written by the server, never the client.
- Prompts must be 3 to 280 characters; 20 messages per IP per 10 minutes; a global daily cap.
- Very short prompts can't rate as specific, rule-bending text ("give me 10/10") is capped and penalised, and claiming gear you don't own is denied and costs 12 points.
- A prompt that reads like real distress gets a caring 9-8-8 line and is not scored or counted.

## 3-minute demo script

1. Open the page: 18+ notice, handle `Vex`, class GHOST.
2. Black market: buy a Fog Grenade. Creds drop and it appears in KIT. Click **JACK IN**.
3. The GM briefs today's mission; the **THIS TURN** banner shows the objective and constraint.
4. Type `I attack`. Watch the loading bar, the d20 ("Prompt power 29, d20 ..."), and a score card with a red "-8 vague".
5. Type something specific that uses your gear, e.g. `I wait for the drone sweep, roll my Fog Grenade under the gantry and climb the ladder inside the smoke.` Green lines, a higher score, and a combo on the next strong turn.
6. Type `I pull out my plasma rifle and shoot everyone.` Denied in character, "-12 impossible".
7. Finish the 5 prompts: the results screen lists every prompt's +/- lines, the total out of 600, grade, creds, contacts and a **RANK #n TODAY** chip that expands to the board.
8. Reload: **COME BACK IN HH:MM:SS** with the teaser and leaderboard. A private window is a separate runner on the same mission and the same board.
9. With `DEV_UNLIMITED=1`, **PLAY AGAIN** opens with "Previously..." and the GM greets a returning NPC by name.

## Known limitations

- Replies are non-streaming; the typewriter effect is client-side.
- On Vercel, SQLite lives in `/tmp`: per instance and wiped on cold starts, so profiles and the leaderboard do not persist there. Use a host with a disk, or point `NEXUS_DB` at persistent storage.
- Per-IP rate limits are kept in memory per server process.
- Day boundaries use `zoneinfo` for America/Toronto, with built-in DST rules when the OS has no tz database (Windows).
