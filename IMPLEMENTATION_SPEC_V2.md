# Pixel Town v2 — Implementation Spec for Claude Code

> **Audience:** Claude Code. Implement exactly what is written here, in the order written.
> **Supersedes:** `IMPLEMENTATION_SPEC.md` (v1). Do not use v1 files.
> **Key change from v1:** There is **no dependency on any "Pixel Agents / Pixel Office" extension**. The project ships its **own** pixel-art web renderer. Everything about the world (towns, offices, characters, rules) lives in **data files**, so it can be changed without touching code.

---

## 0. Rules for Claude Code (read first, obey strictly)

1. **Follow this document to the letter.** Do not rename files, keys, endpoints, events, variables, or amounts.
2. **Do not add features** not specified (no user chat with characters, no accounts, no database, no pathfinding, no frontend framework or build step, no extra dependencies).
3. **Work in phases A → B → C → D.** Do not start a phase until the previous phase's **verification gate** passes.
4. **Copy verbatim** every JSON file, code block, and config in this document marked *verbatim*. Where only behavior is described, implement that behavior exactly.
5. **If anything is ambiguous or you think something is wrong, STOP and ask the user.** Do not guess.
6. **Never hardcode or print the API key.** `GEMINI_API_KEY` is read from the environment only. **Never call the real Gemini API during verification**; use `LLM_MODE=mock`.
7. **Never put world content in Python.** Names, personas, locations, money amounts, and rules come from `worlds/<id>/*.json`. Python contains only generic logic.
8. **Model output is untrusted.** Never insert it into the page with `innerHTML`; use `textContent` only.
9. When finished, report: files created, commands run, and the result of each verification gate.

---

## 1. Goal and Scope

A shared, always-evolving pixel-art town viewable in a browser.

- **Worlds:** a folder `worlds/<world_id>/` defines one town: map, locations (offices, tavern, homes…), characters, and rules. Multiple worlds can exist side by side.
- **Characters** come in three kinds:
    - `llm` — every turn decided by Gemini (structured JSON output).
    - `scripted` — an NPC with fixed schedule and canned lines. **Zero API calls.**
    - `hybrid` — scripted by default; uses Gemini only when another `llm`/`hybrid` character is in the same location at its turn.
- **Rules** (payday, rent, loans, drinking, hangovers) are **data** (`rules.json`), interpreted by a small generic engine.
- **One shared simulation per world.** All viewers watch the same town (fixed Gemini cost).
- **Out of scope:** viewers talking to characters, per-visitor simulations, pathfinding, sound, authentication beyond the admin token, databases.

---

## 2. File Tree (create exactly this)

```
pixel-town/
├── README.md
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── worlds/
│   └── pixel-town/
│       ├── world.json
│       ├── rules.json
│       └── characters/
│           ├── greg.json
│           ├── bob.json
│           ├── charlie.json
│           ├── mabel.json
│           └── dina.json
├── engine/
│   ├── __init__.py
│   ├── models.py        # pydantic models for world/character/rules/state
│   ├── loader.py        # load_world(), load_all_worlds(), reconcile_state()
│   ├── validate.py      # CLI: python -m engine.validate [world_id ...]
│   ├── dsl.py           # conditions, value refs, effects (generic rule interpreter)
│   ├── prompts.py       # build_prompt()
│   ├── llm.py           # GeminiClient, MockClient, CallBudget, parse_turn  (verbatim below)
│   ├── npc.py           # scripted decisions (seeded RNG)
│   ├── hub.py           # pub/sub for SSE                                     (verbatim below)
│   └── simulation.py    # Simulation class (the tick loop)
├── server/
│   ├── __init__.py
│   ├── app.py           # FastAPI app
│   └── static/
│       ├── index.html
│       ├── style.css
│       ├── app.js       # bootstrap, SSE, state store
│       ├── renderer.js  # canvas map + characters + DOM labels/bubbles
│       ├── sprites.js   # procedural pixel sprites
│       └── hud.js       # side panel: clock, globals, characters, chat, admin
├── tests/
│   ├── conftest.py
│   ├── test_dsl.py
│   ├── test_llm_parse.py
│   ├── test_simulation.py
│   └── test_validate.py
└── runtime/             # GENERATED at run time; gitignored; do not create by hand
```

---

## 3. Tech, Dependencies, Environment

- Python **3.10+**. Frontend: plain HTML/CSS/ES modules, Canvas 2D. **No CDN, no build step, no npm.**
- Default model: `gemini-2.5-flash` (set in `world.json`, overridable per character).

**`requirements.txt` (verbatim)**
```
google-genai>=1.0.0
fastapi>=0.110
uvicorn>=0.29
pydantic>=2.6
```

**`requirements-dev.txt` (verbatim)**
```
-r requirements.txt
pytest>=8.0
httpx>=0.27
```

**Environment variables**

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Required when `LLM_MODE=gemini`. Never logged. |
| `LLM_MODE` | `gemini` | `gemini` or `mock` (mock uses each character's `mock_plan`; no network). |
| `ADMIN_TOKEN` | *(unset)* | Enables pause/resume/reset. If unset, control endpoints return 403. |
| `ALWAYS_RUN` | `false` | If `false`, a world only advances while ≥1 viewer is connected. |
| `MAX_LLM_CALLS_PER_HOUR` | `600` | Global cap per world (sliding window). `0` = unlimited. |
| `TIME_SCALE` | `1.0` | Multiplies every sleep. `0` = no sleeping (tests only). |
| `RUNTIME_DIR` | `runtime` | Where `state.json` and `chat.log` are written. |
| `PORT` | `8000` | HTTP port (used by Docker CMD). |

**`.env.example` (verbatim)** — reference only; the app does **not** load `.env` files (no python-dotenv).
```
# Set these in your shell or hosting dashboard. The app does not read this file.
GEMINI_API_KEY=
LLM_MODE=gemini
ADMIN_TOKEN=
ALWAYS_RUN=false
MAX_LLM_CALLS_PER_HOUR=600
TIME_SCALE=1.0
RUNTIME_DIR=runtime
```

**`.gitignore` (verbatim)**
```
.env
runtime/
__pycache__/
*.pyc
.pytest_cache/
.venv/
```

**`.dockerignore` (verbatim)**
```
.git
.env
runtime/
tests/
__pycache__/
*.pyc
.pytest_cache/
.venv/
```

---

# PHASE A — Data-Driven Engine

## A1. World data files (create verbatim)

### `worlds/pixel-town/world.json`
```json
{
  "id": "pixel-town",
  "name": "Pixel Town",
  "seed": 42,
  "map": { "width": 28, "height": 18, "tile_size": 16 },
  "clock": {
    "slots": ["Morning", "Afternoon", "Night"],
    "turn_gap_seconds": 3,
    "slot_gap_seconds": 4
  },
  "history_lines": 6,
  "default_model": "gemini-2.5-flash",
  "turn_order": ["charlie", "greg", "bob", "mabel", "dina"],
  "globals": { "threat_level": "Peaceful", "town_treasury": 200 },
  "locations": {
    "security_desk":       { "name": "Security Desk",       "type": "office",   "color": "#3d4f7a", "rect": { "x": 2,  "y": 2,  "width": 3, "height": 2 } },
    "engineering_benches": { "name": "Engineering Benches", "type": "workshop", "color": "#6b5a3a", "rect": { "x": 10, "y": 5,  "width": 6, "height": 4 } },
    "local_tavern":        { "name": "Local Tavern",        "type": "tavern",   "color": "#7a3d3d", "rect": { "x": 20, "y": 12, "width": 4, "height": 3 } },
    "charlie_home":        { "name": "Charlie's Room",      "type": "home",     "color": "#4a6a5a", "rect": { "x": 2,  "y": 10, "width": 4, "height": 3 } },
    "landlord_office":     { "name": "Landlord's Office",   "type": "office",   "color": "#5a4a6a", "rect": { "x": 10, "y": 13, "width": 5, "height": 3 } }
  }
}
```
> `security_desk`, `engineering_benches`, `local_tavern` keep the coordinates from the original design. `charlie_home` and `landlord_office` are additions so "stay home" and the landlord NPC are visible.
> Location **capacity = rect.width × rect.height** (derived; not stored).

### `worlds/pixel-town/rules.json`
```json
{
  "actions": {
    "none": {
      "description": "Do nothing special.",
      "timing": "immediate",
      "params": [],
      "effects": []
    },
    "lend_money": {
      "description": "Give another character cash (1 to 10 dollars). Use target and amount.",
      "timing": "immediate",
      "params": ["target", "amount"],
      "limits": { "amount": { "min": 1, "max": 10, "default": 10 } },
      "requires": [ { "path": "actor.cash", "op": "gte", "value": "$amount" } ],
      "effects": [
        { "transfer": { "from": "actor.cash", "to": "target.cash", "amount": "$amount" } },
        { "log": "Financial Action: {actor} lent {target} ${amount}." }
      ],
      "fail_log": "Financial Action: {actor} wanted to lend money but cannot afford it."
    },
    "drink": {
      "description": "Go to the tavern and drink for 15 dollars, which gets you wasted. Only works at Night and only if you still have at least 15 dollars after rent is paid.",
      "timing": "deferred",
      "only_slots": ["Night"],
      "params": [],
      "requires": [ { "path": "actor.cash", "op": "gte", "value": 15 } ],
      "effects": [
        { "add": { "path": "actor.cash", "amount": -15 } },
        { "set": { "path": "actor.sobriety", "value": "Wasted" } },
        { "move": { "who": "actor", "to": "local_tavern" } },
        { "log": "Social Action: {actor} went to the tavern and got wasted." }
      ],
      "fail_log": "Social Action: {actor} tried to buy a drink but was rejected for being broke."
    }
  },
  "events": [
    {
      "id": "payday",
      "when": { "slots": ["Afternoon"] },
      "phase": "main",
      "for_each": "stat:daily_pay",
      "branches": [
        { "effects": [
          { "transfer": { "from": "global.town_treasury", "to": "actor.cash", "amount": "@actor.daily_pay" } }
        ] }
      ]
    },
    {
      "id": "rent",
      "when": { "slots": ["Night"] },
      "phase": "main",
      "for_each": "stat:rent_due",
      "branches": [
        {
          "if": [ { "path": "actor.cash", "op": "gte", "value": "@actor.rent_due" } ],
          "effects": [
            { "transfer": { "from": "actor.cash", "to": "global.town_treasury", "amount": "@actor.rent_due" } },
            { "log": "Financial Action: {actor} successfully paid their landlord." },
            { "set": { "path": "actor.rent_due", "value": 20 } }
          ]
        },
        {
          "effects": [
            { "add": { "path": "actor.rent_due", "amount": 10 } },
            { "log": "CRISIS: {actor} missed rent! Landlord added a $10 penalty." }
          ]
        }
      ]
    },
    {
      "id": "wasted_to_hungover",
      "when": { "slots": ["Night"] },
      "phase": "cleanup",
      "for_each": "stat:sobriety",
      "branches": [
        { "if": [ { "path": "actor.sobriety", "op": "eq", "value": "Wasted" } ],
          "effects": [ { "set": { "path": "actor.sobriety", "value": "Hungover" } } ] }
      ]
    },
    {
      "id": "hangover_recovery",
      "when": { "slots": ["Afternoon"] },
      "phase": "cleanup",
      "for_each": "stat:sobriety",
      "branches": [
        { "if": [ { "path": "actor.sobriety", "op": "eq", "value": "Hungover" } ],
          "effects": [ { "set": { "path": "actor.sobriety", "value": "Sober" } } ] }
      ]
    }
  ]
}
```

### `worlds/pixel-town/characters/greg.json`
```json
{
  "id": "greg",
  "name": "Greg",
  "kind": "llm",
  "sprite": { "style": "security_guard" },
  "home": "security_desk",
  "schedule": { "Morning": "security_desk", "Afternoon": "security_desk", "Night": "security_desk" },
  "stats": {},
  "public_stats": [],
  "allowed_actions": ["none"],
  "can_move": false,
  "persona": "You are Greg, the strict Security Guard of Pixel Town. Monitor local threat variables. If it is Morning and Charlie is Hungover, aggressively order him to quit slacking. If it is Night and Charlie is at the tavern Acting Wasted, yell at him to keep it down. Keep responses under 2 sentences.",
  "mock_plan": { "Morning": { "say": "[mock] Eyes up, Charlie. Get to work.", "action": "none" } }
}
```

### `worlds/pixel-town/characters/bob.json`
```json
{
  "id": "bob",
  "name": "Bob",
  "kind": "llm",
  "sprite": { "style": "engineer_blue" },
  "home": "engineering_benches",
  "schedule": { "Morning": "engineering_benches", "Afternoon": "engineering_benches", "Night": "engineering_benches" },
  "stats": { "cash": 50, "current_project": "None", "daily_pay": 25 },
  "public_stats": ["cash"],
  "allowed_actions": ["none", "lend_money"],
  "can_move": false,
  "persona": "You are Bob, a highly disciplined, straight-laced municipal engineer. You think Charlie is an irresponsible slacker. If Charlie asks you for a loan, evaluate your own cash pool and his sobriety. If he is hungover or wasted, reject him coldly. If he is sober and desperate, you may lend him cash with strings attached. Keep responses under 2 sentences.",
  "mock_plan": { "Morning": { "say": "[mock] Fine. Ten dollars. Do not waste it.", "action": "lend_money", "target": "charlie", "amount": 10 } }
}
```

### `worlds/pixel-town/characters/charlie.json`
```json
{
  "id": "charlie",
  "name": "Charlie",
  "kind": "llm",
  "sprite": { "style": "laborer_yellow" },
  "home": "charlie_home",
  "schedule": { "Morning": "engineering_benches", "Afternoon": "engineering_benches", "Night": "charlie_home" },
  "stats": { "cash": 15, "sobriety": "Sober", "rent_due": 20, "daily_pay": 15 },
  "public_stats": ["cash", "sobriety", "rent_due"],
  "allowed_actions": ["none", "drink"],
  "can_move": true,
  "persona": "You are Charlie, a town laborer trying to balance your love for nightlife with financial survival. You love drinking at the tavern when you have spare cash, but you are terrified of eviction. Evaluate your wallet balance against your rent_due. If you are broke, you must dynamically attempt to fix it: beg Bob for a loan, ask Greg for paid night-watch overtime, or stay home. If you have excess cash at Night, you will recklessly spend $15 on alcohol. Keep responses under 2 sentences.",
  "mock_plan": {
    "Morning": { "say": "[mock] Bob, can you spare ten bucks?", "action": "none" },
    "Night": { "say": "[mock] Just one drink. What could go wrong?", "action": "drink", "move_to": "local_tavern" }
  }
}
```

### `worlds/pixel-town/characters/mabel.json`
```json
{
  "id": "mabel",
  "name": "Mabel",
  "kind": "scripted",
  "sprite": { "style": "shopkeeper" },
  "home": "local_tavern",
  "schedule": { "Morning": "local_tavern", "Afternoon": "local_tavern", "Night": "local_tavern" },
  "stats": {},
  "public_stats": [],
  "allowed_actions": [],
  "can_move": false,
  "talk_chance": 0.6,
  "lines": {
    "Morning": ["Opening up. Coffee is on the house until noon.", "Another quiet morning at the tavern."],
    "Afternoon": ["Fresh stew on the stove if anyone is hungry.", "Slow day. Even the regulars are working."],
    "Night": ["Last call is whenever I say so!", "Keep it down, folks. The guard is watching."]
  }
}
```

### `worlds/pixel-town/characters/dina.json`
```json
{
  "id": "dina",
  "name": "Dina",
  "kind": "hybrid",
  "sprite": { "style": "landlord" },
  "home": "landlord_office",
  "schedule": { "Morning": "landlord_office", "Afternoon": "engineering_benches", "Night": "local_tavern" },
  "stats": {},
  "public_stats": [],
  "allowed_actions": ["none"],
  "can_move": false,
  "talk_chance": 0.5,
  "lines": {
    "Morning": ["Ledgers do not balance themselves."],
    "Afternoon": ["Just inspecting the premises."],
    "Night": ["One drink, then back to the books."]
  },
  "persona": "You are Dina, the shrewd but fair landlord of Pixel Town. You care about rent being paid on time. If Charlie is around, remind him about his rent_due without being cruel. Keep responses under 2 sentences.",
  "mock_plan": { "Afternoon": { "say": "[mock] Rent is due tonight, Charlie.", "action": "none" } }
}
```

## A2. Schemas (implement as pydantic models in `engine/models.py`)

**World:** `id`, `name`, `seed:int`, `map{width,height,tile_size}`, `clock{slots[],turn_gap_seconds,slot_gap_seconds}`, `history_lines:int`, `default_model`, `turn_order[]`, `globals{}`, `locations{id:{name,type,color,rect{x,y,width,height}}}`.
Location `type` ∈ `office | workshop | tavern | home | plaza`.

**Character:** `id`, `name`, `kind` (`llm|scripted|hybrid`), `sprite{style?, skin?, hair?, outfit?, accessory?}`, `home`, `schedule{slot:location_id}`, `stats{}`, `public_stats[]`, `allowed_actions[]`, `can_move:bool=false`, `model:str|null=null`, `temperature:float=1.0`, `persona:str|null` (required for `llm`/`hybrid`), `lines{slot:[str]}` (required for `scripted`, optional for others), `talk_chance:float=1.0`, `mock_plan{slot:{say?,action?,target?,amount?,move_to?}}={}`.

**Rules:** `actions{id:{description,timing(immediate|deferred),only_slots?,params[],limits?,requires?,effects[],fail_log?}}`, `events[{id,when{slots?},phase(main|cleanup),for_each,branches[{if?,effects[]}]}]`.

**Runtime state (`runtime/<world_id>/state.json`):**
```json
{
  "world_id": "pixel-town",
  "clock": { "day": 1, "slot_index": 0 },
  "globals": { "threat_level": "Peaceful", "town_treasury": 200 },
  "characters": { "charlie": { "location": "engineering_benches", "stats": { "cash": 15, "sobriety": "Sober", "rent_due": 20, "daily_pay": 15 } } },
  "log": [ { "seq": 1, "speaker": "charlie", "name": "Charlie", "text": "…", "day": 1, "slot": "Morning" } ]
}
```
`log` keeps only the last **200** entries. `clock` always names the slot that is **running or about to run**.

## A3. The rule DSL (`engine/dsl.py`)

**Value references** (anywhere a `value`/`amount` is read):
- number, or string → literal;
- string starting with `$` → action parameter (`$amount`, `$target`);
- string starting with `@` → read a state path.
  A literal string that starts with `$` or `@` is not allowed in `value`/`amount`.

**Paths:** `actor.<key>`, `target.<key>`, `char:<id>.<key>`, `global.<key>`. `<key>` is a stat name; `actor.location` / `target.location` / `char:<id>.location` are **readable** pseudo-stats but **never writable** (use the `move` effect). A missing path makes a condition **false** and an effect **error**.

**Conditions** (a list; all must be true):
- `{"path": P, "op": "eq|ne|gt|gte|lt|lte", "value": V}`
- `{"slot_in": ["Night", …]}`

**Effects** (applied in order):
| Effect | Meaning |
|---|---|
| `{"add": {"path": P, "amount": V}}` | numeric add (negative allowed) |
| `{"set": {"path": P, "value": V}}` | assign |
| `{"transfer": {"from": P, "to": P, "amount": V}}` | subtract from `from`, add to `to`. **No balance check** (use `requires`/`if`). |
| `{"move": {"who": "actor"\|"target", "to": LOCATION_ID}}` | set location; ignores capacity |
| `{"log": "text"}` | emit a system line. Placeholders: `{actor}`, `{target}` (display names), `{amount}`. A literal `$` is just text. |

**Atomicity:** apply each action's effects, or each branch's effects, to a working copy of the state and commit only if every effect succeeded. On any effect error: discard the copy, write the error to the server console, continue the simulation.

## A4. Simulation algorithm (`engine/simulation.py`)

**Public interface (implement exactly):**
```python
class Simulation:
    def __init__(self, world, rules, characters, llm_client, runtime_dir, settings, hub): ...
    async def run_slot(self) -> None       # executes exactly ONE slot, advances the clock, saves, publishes
    async def run_forever(self) -> None    # loop; honors pause + viewer gating + reset
    def snapshot(self) -> dict             # see Phase B for shape
    def pause(self) -> None
    def resume(self) -> None
    def request_reset(self) -> None        # applied at the next slot boundary
    state: dict                            # in-memory runtime state (Section A2 shape)
```
`settings` is a dataclass: `time_scale:float`, `always_run:bool`, `max_calls_per_hour:int`.

**Initial state:** `clock={day:1,slot_index:0}`; `globals` = copy of world globals; each character → `{location: schedule[first slot] or home, stats: deep copy}`; `log=[]`.

**Resume + `reconcile_state()` (in `loader.py`):** if `runtime/<id>/state.json` exists, load it, then: add characters present in the world but missing from the state (initial values); drop characters no longer in the world; add missing stat keys from the character file (never overwrite existing values); add missing globals. This is what lets the owner add a character and simply restart.

**`run_slot()` — exact order:**
1. `slot = slots[clock.slot_index]`. Publish `slot_start {day, slot}`.
2. For every character set `location = schedule[slot]` (fall back to `home`). Publish `state`.
3. `deferred = []`. For each character id in **turn order** (`world.turn_order`, then any characters not listed, alphabetically):
   a. `decision = await decide(char)` (see A5).
   b. **Move:** if `decision.move_to` is valid (known location, `char.can_move`, differs from current, and occupancy < `width×height`), set location and publish `move {character, to}`. Otherwise ignore silently.
   c. **Speech:** if `decision.say` is non-empty (strip, max 280 chars): add a log entry, append `[Name]: text` to `chat.log`, publish `speech {speaker, text}`.
   d. **Action:** resolve per A6. `immediate` actions apply now; `deferred` actions are appended to `deferred`.
   e. If the character used a Gemini call this turn **and** it is not the last turn of the slot: `await sleep(turn_gap_seconds × time_scale)`.
4. Run events with `phase == "main"` whose `when.slots` contains `slot` (or is absent), in file order (A7).
5. Apply `deferred` actions in recorded order (A6).
6. Run events with `phase == "cleanup"` (A7).
7. Advance the clock: `slot_index += 1`; if it passes the last slot → `slot_index = 0`, `day += 1`.
8. Save `state.json` (atomic write: write to temp file, then `os.replace`). Publish `state`.
9. `await sleep(slot_gap_seconds × time_scale)`.

**`run_forever()`:** loop forever: apply pending reset (delete `state.json` and `chat.log`, rebuild initial state, publish `snapshot`); if paused, or (`not always_run` and `hub.viewers == 0`): `await asyncio.sleep(0.5)` and continue; else `await run_slot()`. Exceptions inside a slot are printed and the loop continues after a 5 s sleep (× time_scale).

**Logging:** every `speech` and `system` line is appended to the in-memory log (and `chat.log` as `[Name]: text` or `[System]: text`) and also published. System lines: speaker `"system"`, name `"System"`.

## A5. `decide(char)` and NPC behavior

```
kind scripted → npc.decide_scripted(char)
kind llm      → llm_path(char)
kind hybrid   → llm_path(char) if another character with kind in {llm, hybrid}
                is currently in the same location (at this moment of the turn)
                else npc.decide_scripted(char)
llm_path      → if CallBudget.allow(): build_prompt → client.generate_turn → parse
                else: npc.decide_scripted(char) and (once per budget window)
                      publish notice {level: "warn", text: "LLM call cap reached; characters are quiet until the window resets."}
```
- **`npc.decide_scripted`:** `rng = random.Random(f"{seed}:{day}:{slot}:{char.id}")`. If `lines[slot]` exists and `rng.random() < talk_chance` → `say = rng.choice(lines[slot])`; else `say = ""`. Action is always `none`. No move.
- Characters that make a Gemini call count one call in `CallBudget`.

## A6. Action resolution

For `(actor, decision)`:
1. If `decision.action` not in `actor.allowed_actions` or not in `rules.actions` → treat as `none`.
2. If the action has `only_slots` and the current slot is not listed → treat as `none` (silent).
3. **Params:** if `"target"` in params: target must be an existing character id ≠ actor, else treat as `none` (silent). If `"amount"` in params: use `decision.amount`; if missing/non-integer use `limits.amount.default`; clamp to `[min, max]`.
4. Evaluate `requires` (with params). If any fails: if `fail_log` exists, emit it as a system line; apply nothing.
5. Apply `effects` atomically (A3).

## A7. Events

For each event in the phase: determine actors via `for_each`:
- `"global"` → run once with no actor (effects may only use `global.` and `char:<id>.` paths);
- `"stat:<name>"` → every character (in turn order) whose `stats` contains `<name>`.
  For each actor, evaluate `branches` in order; apply the **first** branch whose `if` conditions all pass (a branch without `if` always passes); apply at most one branch per actor. Atomic per branch.

## A8. Prompt builder (`engine/prompts.py`)

`build_prompt(char, state, world, rules, characters)` returns this text (fill the `{…}`):

```
{persona}

WORLD
Day {day}, {slot}. {globals as "key: value" pairs, comma-separated}.
You are at: {location name} ({location id}).

YOUR STATS
- {key}: {value}            (one line per own stat; write "none" if the character has no stats)

OTHERS
- {Name} ({kind}) at {location name} — {public stats as "key: value", comma-separated}     (one line per other character; omit the " — …" part if they have no public stats)

AVAILABLE ACTIONS (choose exactly one; use "none" if nothing fits)
- {action id}: {description}     (only the character's allowed_actions)

WALKING
{if can_move: "You may walk to one of these places by setting move_to (or use null to stay put):\n- {id}: {name}  (every location)"}
{else: "You cannot walk anywhere; set move_to to null."}

RECENT HISTORY
{last world.history_lines log entries, one per line, as "[Name]: text"; write "(nothing yet)" if empty}

Respond ONLY with a JSON object with these keys:
"say" (string, in character, under 2 sentences), "action" (string), "target" (string or null, a character id),
"amount" (integer or null), "move_to" (string or null, a location id).
```
History is **re-read from the in-memory log on every call**, so later speakers in a slot see earlier lines from that slot.

## A9. `engine/llm.py` (verbatim)

```python
import asyncio
import json
import re
import time
from collections import deque
from typing import Optional

from pydantic import BaseModel


class TurnSchema(BaseModel):
    """Schema sent to Gemini (no defaults, which some SDK versions reject)."""
    say: str
    action: str
    target: Optional[str]
    amount: Optional[int]
    move_to: Optional[str]


class TurnOutput(BaseModel):
    """Lenient model used when parsing."""
    say: str = ""
    action: str = "none"
    target: Optional[str] = None
    amount: Optional[int] = None
    move_to: Optional[str] = None


NONE_TURN = {"say": "", "action": "none", "target": None, "amount": None, "move_to": None}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_turn(text):
    """Parse model output into a turn dict. Never raises; returns NONE_TURN on failure."""
    if not text:
        return dict(NONE_TURN)
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    candidates = [cleaned]
    match = _OBJ_RE.search(cleaned)
    if match:
        candidates.append(match.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                out = TurnOutput.model_validate(data).model_dump()
                out["say"] = (out["say"] or "").strip()[:280]
                out["action"] = ((out["action"] or "none").strip().lower()) or "none"
                return out
        except Exception:
            continue
    return dict(NONE_TURN)


class CallBudget:
    """Sliding one-hour window. max_per_hour <= 0 means unlimited."""

    def __init__(self, max_per_hour):
        self.max = max_per_hour
        self._stamps = deque()

    def _trim(self, now):
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()

    def allow(self):
        if self.max <= 0:
            return True
        self._trim(time.time())
        return len(self._stamps) < self.max

    def record(self):
        self._stamps.append(time.time())


class GeminiClient:
    def __init__(self):
        from google import genai  # imported lazily so mock mode needs no key
        self._client = genai.Client()  # reads GEMINI_API_KEY from the environment

    async def generate_turn(self, *, char, slot, prompt, model):
        from google.genai import types
        for delay in (0, 2, 5):
            if delay:
                await asyncio.sleep(delay)
            try:
                res = await self._client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TurnSchema,
                        temperature=char.temperature,
                    ),
                )
                return parse_turn(res.text)
            except Exception as exc:  # network, quota, safety, schema...
                print(f"[llm] {char.id}: attempt failed: {exc}")
        return dict(NONE_TURN)


class MockClient:
    """Deterministic, offline. Uses each character's mock_plan[slot]."""

    def __init__(self):
        self.calls = []  # list of (character_id, slot)

    async def generate_turn(self, *, char, slot, prompt, model):
        self.calls.append((char.id, slot))
        plan = (char.mock_plan or {}).get(slot, {})
        out = dict(NONE_TURN)
        out.update({k: v for k, v in plan.items() if k in out})
        return out


def make_client(mode):
    if mode == "mock":
        return MockClient()
    return GeminiClient()
```
> **Known SDK risk:** if the installed `google-genai` rejects `response_schema=TurnSchema` at runtime, remove **only** the `response_schema=` line (keep `response_mime_type`); `parse_turn` already handles it. Tell the user if you do this.

## A10. `engine/hub.py` (verbatim)

```python
import asyncio
import json


class Hub:
    """Per-world pub/sub. Subscribers receive JSON strings."""

    def __init__(self):
        self._subs = set()
        self._seq = 0

    @property
    def viewers(self):
        return len(self._subs)

    def subscribe(self):
        q = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)

    def publish(self, event):
        self._seq += 1
        event = {"seq": self._seq, **event}
        payload = json.dumps(event)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self._subs.discard(q)  # slow client: drop it; EventSource will reconnect
        return event
```

## A11. Validator (`engine/validate.py`)

CLI: `python -m engine.validate [world_id ...]` (no args = every folder in `worlds/`). Exit code `0` if no errors, `1` otherwise.

**Errors (must fail):** invalid JSON; pydantic schema violation; character filename ≠ its `id`; duplicate ids; `turn_order` contains unknown id; `home` or any `schedule` value not a known location; `schedule` key not in `clock.slots`; `allowed_actions` entry not in `rules.actions`; `events[].when.slots` entry not in `clock.slots`; `only_slots` entry not in `clock.slots`; any `move.to` not a known location; `char:<id>` path with unknown id; `$param` used but not in the action's `params`; any write path ending in `.location`; `for_each` not `global` or `stat:<name>`; location `rect` outside the map; two location rects overlapping; more characters scheduled in a location for a slot than `width×height`; `public_stats` not a subset of `stats` keys; `llm`/`hybrid` without `persona`; `scripted` without `lines`.

**Warnings (print, do not fail):** unknown `sprite.style`; more than 8 `llm` characters; character missing from `turn_order` (it is appended automatically).

**Success output (exact format):**
```
OK: <world_id> (<N> characters, <M> locations). Estimated Gemini calls/day: <min>-<max>
```
`min = (#llm) × (#slots)`, `max = (#llm + #hybrid) × (#slots)`.

## A12. Tests (pytest; offline; no network)

`tests/conftest.py` provides fixtures: `world` (loads `worlds/pixel-town`), `sim` (a `Simulation` with `MockClient`, `time_scale=0`, `always_run=True`, a temp `runtime_dir`, a fresh `Hub`), and `sim_no_plans` (same, but every character's `mock_plan` cleared). Use `asyncio.run(...)` inside tests (no pytest-asyncio).

Required tests (all must pass):

| Test | Assertion |
|---|---|
| `test_dsl` | every condition op; `$param` and `@path` resolution; `add`, `set`, `transfer`, `move`, `log`; atomic rollback (a failing 2nd effect leaves state unchanged); writing `.location` is rejected |
| `test_llm_parse` | plain JSON; JSON in ```` ```json ```` fences; JSON surrounded by prose; garbage → `NONE_TURN`; `say` capped at 280 chars; action lower-cased; `amount` given as `"10"` → `10` |
| `test_day1_no_actions` | with `sim_no_plans`, after 3 `run_slot()` calls: `charlie.cash == 10`, `bob.cash == 75`, `town_treasury == 180`, `charlie.sobriety == "Sober"`, `clock == {day: 2, slot_index: 0}` |
| `test_lend_then_drink` | with `sim` (seed mock plans), after Day 1: `charlie.cash == 5`, `charlie.sobriety == "Hungover"`, `bob.cash == 65`, `town_treasury == 180`. After Day 2 (3 more slots): `charlie.cash == 10`, `charlie.sobriety == "Sober"`, `bob.cash == 80`, `town_treasury == 160`, and the log contains the drink-rejected line ("rejected for being broke") |
| `test_hybrid_trigger` | `MockClient.calls` contains `("dina","Afternoon")` and does **not** contain `("dina","Morning")` or `("dina","Night")`, and never contains `("mabel", …)` |
| `test_scripted_determinism` | two fresh sims with the same seed produce identical Mabel lines over 2 days; a different seed produces a different sequence (use seeds chosen so they differ) |
| `test_move_capacity` | moving a 7th character into `security_desk` (capacity 6) is ignored |
| `test_budget` | with `max_calls_per_hour=1`, only one `MockClient` call is made in a slot; others fall back to scripted/none |
| `test_resume` | run 1 slot, build a new `Simulation` from the same `runtime_dir`; its clock equals the saved clock and stats match |
| `test_reconcile_state` | adding a new character file and reloading adds it to the state without changing existing stats |
| `test_validate` | the seed world passes; a copy with a duplicate id, an unknown location, an overlapping rect, and an unknown action each produce an error |

### ✅ GATE A (all must pass before Phase B)
```bash
pip install -r requirements-dev.txt
python -m engine.validate            # prints: OK: pixel-town (5 characters, 5 locations). Estimated Gemini calls/day: 9-15
pytest -q                            # all tests pass
```

---

# PHASE B — Web Server

## B1. Server behavior (`server/app.py`, FastAPI)

**Startup:**
1. Load and validate **all** worlds; on any validation error print the errors and exit with code 1.
2. If `LLM_MODE=gemini` and `GEMINI_API_KEY` is unset → print `ERROR: GEMINI_API_KEY environment variable is not set.` and exit with code 1.
3. Create one `Hub`, one `Simulation` per world (all sharing a single LLM client but separate `CallBudget`s), and start `run_forever()` for each as an asyncio task (use the FastAPI lifespan). On shutdown, cancel tasks and save state.
4. **Run with exactly one worker** (state is in memory).

**Endpoints**

| Method & path | Response |
|---|---|
| `GET /` | `static/index.html` |
| `GET /static/*` | static files |
| `GET /healthz` | `{"ok": true}` |
| `GET /api/worlds` | `[{"id","name"}]` |
| `GET /api/worlds/{id}/definition` | `{id, name, map, slots, locations{id:{name,type,color,rect}}, characters[{id,name,kind,sprite}], turn_order}` — **never includes personas or prompts** |
| `GET /api/worlds/{id}/snapshot` | the snapshot (below) |
| `GET /api/worlds/{id}/stream` | Server-Sent Events (below) |
| `POST /api/worlds/{id}/control` | body `{"action": "pause"\|"resume"\|"reset"}`; header `X-Admin-Token` must equal `ADMIN_TOKEN` (use `secrets.compare_digest`). If `ADMIN_TOKEN` is unset or wrong → `403`. Unknown world → `404`. |

Unknown world id → `404` everywhere.

**Snapshot shape:**
```json
{
  "type": "snapshot",
  "state": {
    "clock": { "day": 1, "slot": "Morning", "slot_index": 0 },
    "globals": { "threat_level": "Peaceful", "town_treasury": 200 },
    "characters": { "charlie": { "location": "engineering_benches", "stats": { "cash": 15 } } }
  },
  "log": [ { "seq": 1, "speaker": "charlie", "name": "Charlie", "text": "…", "day": 1, "slot": "Morning" } ],
  "paused": false
}
```
`log` = last 50 entries.

**SSE stream** (`Content-Type: text/event-stream`, header `Cache-Control: no-cache`):
- Increment viewers on connect; decrement in a `finally`.
- First message: the snapshot. Then every published event, each as `data: <json>\n\n`.
- If nothing arrives for 15 s, send the comment line `: keepalive\n\n`.

**Event types** (all JSON, each has a numeric `seq`):

| `type` | Fields |
|---|---|
| `slot_start` | `day`, `slot` |
| `speech` | `speaker` (character id), `name`, `text` |
| `system` | `text` |
| `move` | `character`, `to` |
| `state` | `state` (same shape as snapshot `state`) |
| `notice` | `level` (`info`\|`warn`), `text` |
| `snapshot` | as above (sent on connect and after reset) |

Pause/resume publish a `notice` (`info`: "Simulation paused." / "Simulation resumed.").

### ✅ GATE B (all must pass before Phase C)
```bash
LLM_MODE=mock TIME_SCALE=0.1 ALWAYS_RUN=true uvicorn server.app:app --port 8000 &
curl -s localhost:8000/healthz                              # {"ok":true}
curl -s localhost:8000/api/worlds                           # lists pixel-town
curl -s localhost:8000/api/worlds/pixel-town/definition     # contains no "persona"
curl -s localhost:8000/api/worlds/pixel-town/snapshot
curl -N --max-time 5 localhost:8000/api/worlds/pixel-town/stream | head -n 20   # first line is a snapshot, then events
curl -s -X POST localhost:8000/api/worlds/pixel-town/control -H 'Content-Type: application/json' -d '{"action":"pause"}'   # 403 (no ADMIN_TOKEN)
```
Then restart with `ADMIN_TOKEN=test` and confirm pause with the header succeeds (`200`) and resume works. Stop the server.

---

# PHASE C — Pixel Renderer (own implementation; no extension)

## C1. Page layout (`index.html`, `style.css`)

- Two regions: **map** (canvas) and **side panel**. Desktop: side by side (panel 340 px wide). Narrow screens (< 900 px): stacked, map first.
- Fixed dark UI (background `#14161c`, text `#e6e6e6`). `lang="en"`, viewport meta tag.
- Canvas internal size = `map.width × tile_size` by `map.height × tile_size` (448×288 for the seed world). CSS: `width: 100%`, `height: auto`, `image-rendering: pixelated`. Call `ctx.imageSmoothingEnabled = false`.
- Name labels, speech bubbles, and location labels are **DOM elements** absolutely positioned over the canvas wrapper using **percentage** coordinates. Always set text via `textContent`.
- JS files are ES modules (`<script type="module" src="/static/app.js">`).

## C2. Rendering rules (`renderer.js`, `sprites.js`)

**Ground:** fill `#2e3a2e`; draw a subtle checker with `#334433` on alternating tiles.

**Locations:** for each location, fill its rect with the location `color` using a 2-shade checker (second shade = color lightened ~8%), 1 px darker border. Add simple props with filled rectangles, distinguishable by `type`:
`office` → a desk (2×1 tiles) with a small monitor; `workshop` → rows of benches (every other row); `tavern` → a bar counter along the top row plus two small tables; `home` → a bed (2×1) with a pillow; `plaza` → a fountain square. Exact look is free, but each type must look different. Draw a DOM label with the location name at the rect's top-left.

**Slot positions:** characters in the same location are sorted by `turn_order`; the *i*-th gets cell `(rect.x + i % width, rect.y + floor(i / width))` (wrap within the rect if more than `width×height`). Sprite is horizontally centered in its tile with feet at the tile's bottom edge.

**Movement:** each character has a render position (logical px). It moves in a **straight line** toward its target at **48 logical px/s** (3 tiles/s). While moving, toggle walk frames every 150 ms; when idle use frame 0. If `prefers-reduced-motion: reduce`, teleport instead. Animate with `requestAnimationFrame`. Draw characters sorted by y so lower ones overlap higher ones.

**Time of day overlay** (full-canvas, drawn last), by slot name: `Morning` none; `Afternoon` `rgba(255,160,0,0.08)`; `Night` `rgba(10,10,60,0.35)`; unknown slot names none.

**Sprites (`sprites.js`)** — drawn procedurally on a 12×16 grid with `fillRect` (no image files):

| Part | Rect (x, y, w, h) | Color |
|---|---|---|
| Hair | 3, 0, 6, 2 | `hair` |
| Head | 3, 2, 6, 5 | `skin` |
| Eyes | 4, 4, 1, 1 and 7, 4, 1, 1 | `#111111` |
| Torso | 2, 7, 8, 5 | `outfit` |
| Arms | 1, 7, 1, 4 and 10, 7, 1, 4 | `skin` |
| Legs frame 0 | 3, 12, 2, 4 and 7, 12, 2, 3 | `#2b2b2b` |
| Legs frame 1 | 3, 12, 2, 3 and 7, 12, 2, 4 | `#2b2b2b` |

Accessories: `cap` → (2,0,8,2) darker outfit + brim (2,2,8,1); `hardhat` → (2,0,8,3) `#f5c518`; `apron` → (3,9,6,3) `#f2f2f2`; `badge` → (7,8,2,2) `#ffd700`; `none` → nothing.

Style presets (explicit `sprite` fields override them; defaults `skin #f1c27d`, `hair #3b2f2f`; unknown style → outfit `#888888`, accessory `none`):

| style | outfit | accessory |
|---|---|---|
| `security_guard` | `#2b3a67` | `cap` |
| `engineer_blue` | `#2f6fdb` | `hardhat` |
| `laborer_yellow` | `#e0b400` | `none` |
| `shopkeeper` | `#8e44ad` | `apron` |
| `landlord` | `#7a3b3b` | `badge` |

**Name label:** small DOM text under each sprite (character name). **Kind marker:** `scripted` NPCs get a small grey dot before the name; `hybrid` a half-filled marker (any distinguishable markers are fine, but they must differ).

**Speech bubbles:** DOM element above the sprite, max 3 lines (wrap ~22 chars/line, truncate with `…`), visible **6 seconds**, replaced by a newer bubble from the same character. Bubble background `#f5f5f5`, text `#111`.

## C3. Side panel (`hud.js`)

1. **Header:** world name; clock `Day N — Slot` with an icon (`Morning` 🌅, `Afternoon` ☀️, `Night` 🌙; fallback 🕒); connection dot (green connected, red disconnected); world selector `<select>` only if `/api/worlds` returns more than one.
2. **Banner area:** shows the latest `notice` (warn = amber, info = blue), dismissible.
3. **Globals:** one row per global (`key: value`).
4. **Characters:** a card per character: name, kind badge (`LLM` / `NPC` / `Hybrid`), current location name, all stats as `key: value`.
5. **Chat log:** last 50 entries, newest at the bottom, auto-scroll when the user is at the bottom; speaker names colored with that character's outfit color; `system` lines in grey italics. Container has `aria-live="polite"` and `role="log"`.
6. **Admin:** a collapsed `<details>` titled "Admin" containing a password input for the token, and Pause / Resume / Reset buttons that `POST` to `/control` with the `X-Admin-Token` header. Keep the token in memory only. Show the server's error text if rejected.

## C4. Client state (`app.js`)

- World id from `?world=<id>`; otherwise the first world returned by `/api/worlds`.
- Load `/definition`, then open `EventSource('/api/worlds/<id>/stream')`.
- `snapshot` → replace all state and chat; `slot_start` → update clock; `speech` → append chat + bubble; `system` → append chat; `move` → update that character's location; `state` → replace state; `notice` → banner.
- `EventSource` reconnects automatically; the first event after reconnect is a new `snapshot` which replaces local state. Disconnected → red dot.

### ✅ GATE C (manual; Claude Code reports, user confirms in a browser)
Claude Code runs the Gate B server in mock mode and verifies via `curl` that `/`, `/static/app.js`, `/static/renderer.js`, `/static/sprites.js`, `/static/hud.js`, `/static/style.css` all return `200`. Then it gives the user this checklist to confirm in a browser at `http://localhost:8000`:
- [ ] Five location areas are drawn with different looks; labels are readable
- [ ] Five sprites appear with different outfits and accessories; names under them
- [ ] The clock advances Morning → Afternoon → Night → next Day
- [ ] Characters walk (not teleport) between locations at slot changes; Charlie walks to the tavern at Night
- [ ] Speech bubbles appear over speakers for ~6 s; chat log fills; system lines are grey italics
- [ ] Stats in the character cards change (Charlie's cash, sobriety)
- [ ] Night tint is visible at Night
- [ ] Resizing to a phone width stacks the panel under the map
- [ ] Admin pause/resume works with the token; fails without it

---

# PHASE D — Deployment Files and README

## D1. `Dockerfile` (verbatim)
```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine ./engine
COPY server ./server
COPY worlds ./worlds
ENV RUNTIME_DIR=/app/runtime
VOLUME ["/app/runtime"]
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
```

## D2. Deployment rules (put these in the README)
- The app is a **long-running server**. It must run as **one instance, one worker**. **Do not use serverless platforms** (Vercel/Netlify functions, Cloud Functions, Lambda).
- Suitable: any container host with a persistent disk (Render, Fly.io, Railway), or a VPS with Docker.
- Mount a **persistent volume at `/app/runtime`** or the world resets on every deploy.
- Set env vars in the host dashboard: `GEMINI_API_KEY` (secret), `ADMIN_TOKEN` (secret), and optionally `ALWAYS_RUN`, `MAX_LLM_CALLS_PER_HOUR`.
- Health check path: `/healthz`. HTTPS is provided by the host.
- The API key stays on the server; the browser never sees it.
- **Cost control:** by default a world only advances while someone is watching, and `MAX_LLM_CALLS_PER_HOUR` caps calls. Set `ALWAYS_RUN=true` only if you accept continuous Gemini usage.

## D3. `README.md` (required sections)
1. **What this is** (3–5 sentences).
2. **Run locally** — mock mode (`LLM_MODE=mock`) and real mode; `python -m engine.validate`; `pytest -q`.
3. **Authoring guide** — copy Section 9 below.
4. **Deploy** — copy Section D2 plus the Docker commands.

### ✅ GATE D
```bash
docker build -t pixel-town .      # skip and say so if Docker is not installed
```
If Docker is unavailable, state that and confirm the Dockerfile matches Section D1 exactly.

---

## 9. Authoring Guide (how to change things without touching code)

After **any** change run `python -m engine.validate` and fix errors before starting the server.

| I want to… | Do this |
|---|---|
| **Add a character** | Copy a file in `characters/`, set a new `id` (filename must equal `id`), `name`, `kind`, `sprite`, `home`, `schedule`, `stats`, `persona` (or `lines` for NPCs). Optionally add the id to `turn_order`. Restart the server (existing worlds keep their saved state; the new character is added automatically). |
| **Add a free NPC** | Same, with `"kind": "scripted"`, `lines`, and `talk_chance`. It costs **no** Gemini calls. |
| **Add an NPC that sometimes thinks** | `"kind": "hybrid"` with both `persona` and `lines`. Gemini is used only when another `llm`/`hybrid` character shares its location. |
| **Give a character a salary / rent** | Add `daily_pay` / `rent_due` to its `stats`. The `payday` and `rent` events pick it up automatically. |
| **Add a location (office, shop, …)** | Add an entry to `locations` in `world.json` (unique id, `type`, `color`, a `rect` inside the map that overlaps nothing). Reference it from characters' `home` / `schedule`. |
| **Make the map bigger** | Change `map.width` / `map.height` in `world.json`. The renderer adapts. |
| **Create another town** | Copy `worlds/pixel-town/` to `worlds/<new-id>/`, change `id`/`name`, edit contents. It appears in the world selector. |
| **Change money amounts / add a rule** | Edit `rules.json` (actions and events use the DSL in Section A3). Add the new action id to the characters' `allowed_actions`. |
| **Change a character's personality** | Edit `persona`. |
| **Use a cheaper model for a minor role** | Set `"model": "gemini-2.5-flash-lite"` (or any valid model id) on that character. |
| **Speed up / slow down time** | Edit `clock.turn_gap_seconds` / `slot_gap_seconds`, or set `TIME_SCALE`. |
| **Add a time slot** | Add it to `clock.slots`; add schedule entries, events, and a time-of-day tint mapping if desired. |

**Cost guide:** Gemini calls per day ≈ (#`llm` characters + #active `hybrid`) × (#slots). Keep most of the cast `scripted` or `hybrid`; the validator prints the estimate.

---

## 10. Definition of Done

- [ ] File tree matches Section 2 exactly (plus generated `runtime/`)
- [ ] All verbatim files match this document
- [ ] No world content (names, personas, amounts, locations) exists in Python or JS
- [ ] Gates A, B, C (checklist handed to the user), and D completed and reported
- [ ] `pytest -q` passes; `python -m engine.validate` prints the OK line
- [ ] No real Gemini call was made during verification; no key stored anywhere
- [ ] No extension, CDN, or build tool is required
- [ ] README contains the four required sections