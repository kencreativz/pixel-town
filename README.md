# Pixel Town

## What this is
Pixel Town is a shared, always-evolving pixel-art town that you watch in a browser. Each world lives in
`worlds/<world_id>/` as data files: map, locations, characters and rules. A small generic engine runs one
shared simulation per world. Characters are `llm` (every turn decided by Gemini), `scripted` (fixed schedule and
canned lines, zero API calls) or `hybrid` (scripted unless another `llm`/`hybrid` character shares its location).
The page is plain HTML, CSS and ES modules drawn on a Canvas, with no build step.

## Run locally
Requires Python 3.10+.

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-dev.txt

Check the worlds and run the tests (offline, no network):

    python -m engine.validate        # prints an "OK: <world_id> (...)" line per world
    pytest -q

Quick start: `bin/pixeltown mock` or `bin/pixeltown real`. `real` takes `GEMINI_API_KEY` from the shell, else from
`.env.development.local` or `.env` (both git-ignored; a deliberate deviation from "never in a file in this repo"),
else asks for it; it runs the clock 5× slower with a 30 calls/hour cap. `bin/pixeltown local` uses a local model
through Ollama (start the Ollama app first; model from `OLLAMA_MODEL`, default `ssfdre38/gemma4-turbo`). `real` and `local`
pause while no browser tab is open. To type just
`pixeltown`, add the folder to your PATH once:
`echo 'export PATH="$HOME/Documents/personal/practice/pixel-town/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc`

Mock mode (no Gemini calls, no key needed; characters use their `mock_plan`):

    LLM_MODE=mock uvicorn server.app:app --port 8000

Real mode: set `GEMINI_API_KEY` in your shell environment (never in a file in this repo), then:

    uvicorn server.app:app --port 8000      # LLM_MODE defaults to gemini

Open http://localhost:8000. Optional: set `ADMIN_TOKEN` to enable pause/resume/reset from the Admin panel.
Always run a single worker. Environment variables are listed in `.env.example`; the app does not read `.env` files.

## Authoring guide

After **any** change run `python -m engine.validate` and fix errors before starting the server.

| I want to… | Do this |
|---|---|
| **Add a character** | Copy a file in `characters/`, set a new `id` (filename must equal `id`), `name`, `kind`, `sprite`, `home`, `schedule`, `stats`, `persona` (or `lines` for NPCs). Optionally add the id to `turn_order`. Restart the server (existing worlds keep their saved state; the new character is added automatically). |
| **Add a free NPC** | Same, with `"kind": "scripted"`, `lines`, and `talk_chance`. It costs **no** Gemini calls. |
| **Add an NPC that sometimes thinks** | `"kind": "hybrid"` with both `persona` and `lines`. Gemini is used only when another `llm`/`hybrid` character shares its location. |
| **Give a character a salary / rent** | Add `daily_pay` / `rent_due` to its `stats`. The `payday` and `rent` events pick it up automatically. `payday` pays into the character's `bank` stat (give it one), so also allow `withdraw` / `deposit`: they work only at the Town Bank (`town_bank`), so the character needs `can_move: true`. |
| **Add a guard / a rule people can break** | Give the guard `"guard": true` and `can_move: true`. Guards skip normal turns. After each slot they walk up to everyone who matches an entry in `violations` in `rules.json` (`id`, `text` for the LLM, fallback `say`, `if` conditions) and tell them off. |
| **Add a landlord / rounds** | Give the character `"rounds": { "stat": "rent_due", "slot": "Afternoon" }` and `can_move: true`. In that slot it skips its normal turn and walks up to each awake character with that stat, one by one, and talks with them face to face. |
| **Conversations** | Anyone spoken to face to face answers. The two take turns (up to 6 replies) until one of them ends the talk. |
| **Local-mode-only stats** | `local_stats` in a character file merge into its `stats` only with `LLM_MODE=ollama` (e.g. rent for everyone but the landlord; `cash_pay` pays straight into cash). |
| **Add a location (office, shop, …)** | Add an entry to `locations` in `world.json` (unique id, `type`, `color`, a `rect` inside the map that overlaps nothing). Reference it from characters' `home` / `schedule`. Add `map.roads` rects that reach its two doors (sprites walk only on roads outside rooms). |
| **Make the map bigger** | Change `map.width` / `map.height` in `world.json`. The renderer adapts. |
| **Create another town** | Copy `worlds/pixel-town/` to `worlds/<new-id>/`, change `id`/`name`, edit contents. It appears in the world selector. |
| **Change money amounts / add a rule** | Edit `rules.json` (actions and events use the DSL in Section A3). Add the new action id to the characters' `allowed_actions`. |
| **Change a character's personality** | Edit `persona`. |
| **Use a cheaper model for a minor role** | Set `"model": "gemini-2.5-flash-lite"` (or any valid model id) on that character. |
| **Speed up / slow down time** | Edit `clock.turn_gap_seconds` / `slot_gap_seconds`, or set `TIME_SCALE`. `clock.walk_seconds_per_tile` is how long the simulation waits per tile for sprites to walk before they talk and act (0 = no wait). |
| **Add a time slot** | Add it to `clock.slots`; add schedule entries, events, and a time-of-day tint mapping if desired. |

**Cost guide:** Gemini calls per day ≈ (#`llm` characters + #active `hybrid`) × (#slots). Keep most of the cast `scripted` or `hybrid`; the validator prints the estimate.

## Deploy
- The app is a **long-running server**. It must run as **one instance, one worker**. **Do not use serverless platforms** (Vercel/Netlify functions, Cloud Functions, Lambda).
- Suitable: any container host with a persistent disk (Render, Fly.io, Railway), or a VPS with Docker.
- Mount a **persistent volume at `/app/runtime`** or the world resets on every deploy.
- Set env vars in the host dashboard: `GEMINI_API_KEY` (secret), `ADMIN_TOKEN` (secret), and optionally `ALWAYS_RUN`, `MAX_LLM_CALLS_PER_HOUR`.
- Health check path: `/healthz`. HTTPS is provided by the host.
- The API key stays on the server; the browser never sees it.
- **Cost control:** by default a world only advances while someone is watching, and `MAX_LLM_CALLS_PER_HOUR` caps calls. Set `ALWAYS_RUN=true` only if you accept continuous Gemini usage.

Docker:

    docker build -t pixel-town .
    docker run -p 8000:8000 -v pixel-town-runtime:/app/runtime -e GEMINI_API_KEY -e ADMIN_TOKEN pixel-town

`-e GEMINI_API_KEY` and `-e ADMIN_TOKEN` pass the values from your shell, so no key is written into the command.
Add `-e LLM_MODE=mock` to run without Gemini.
