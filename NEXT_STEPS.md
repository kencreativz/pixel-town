# Pixel Town — What's Left To Do

All four phases (A, B, C, D) are built. These are the steps **you** still need to do yourself.
Do them in order: each step is short, and you can stop after any of them.

> **How to read this file:** grey boxes are commands. Copy each box into your terminal and press Enter.
> Lines starting with `#` are notes, so you don't need to type them.

---

## Before you start (do this every time you open a new terminal)

Go into the project folder:

```bash
cd ~/Documents/personal/practice/pixel-town
```

Every command in this guide starts with **`.venv/bin/`** (for example `.venv/bin/python`). That runs the project's
own Python 3.12, which is inside the `.venv` folder. Your Mac has no plain `python` command, and its built-in
`python3` is 3.9, which is too old for this project.

> **Why not just type `python`?** If you do, zsh says `command not found: python`. Plain `python` only works after
> you run `source .venv/bin/activate` in that same terminal. The `.venv/bin/...` commands below work either way, so
> you can skip activation entirely.

**Quick health check** (optional, takes 2 seconds). Paste the line and press Enter:

```bash
.venv/bin/python -m engine.validate && .venv/bin/pytest -q
```

This runs two checks, one after the other. The tests only run if the first check passes.
You should see:
- `OK: pixel-town (6 characters, 6 locations). Estimated Gemini calls/day: 9-12`
- `40 passed`

**If you see `no such file or directory: .venv/bin/python`,** you're not in the project folder. Run the `cd` line above
again. If it still fails, the `.venv` folder is missing, so rebuild it once:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

---

## Step 1 — Look at the town in your browser (Gate C check)

This is the only check nobody has done yet. It's free, because it uses fake ("mock") characters and no Gemini.

### 1a. (Optional) Start the town fresh from Day 1

Earlier test runs left the town at about Day 10. To start over:

```bash
rm -rf runtime/pixel-town
```

### 1b. Start the server

```bash
LLM_MODE=mock TIME_SCALE=1.0 ALWAYS_RUN=true ADMIN_TOKEN=test .venv/bin/uvicorn server.app:app --port 8000
```

What each part means:

| Part | Meaning |
|---|---|
| `LLM_MODE=mock` | Fake characters, no Gemini, no cost |
| `TIME_SCALE=1.0` | Normal speed, so you can see walking and speech bubbles |
| `ALWAYS_RUN=true` | The town keeps running even with no browser open |
| `ADMIN_TOKEN=test` | The password for the Admin buttons is `test` |

Leave this terminal open. The server runs until you press **Ctrl + C**.

### 1c. Open the page

Go to **http://localhost:8000** in your browser.

### 1d. Tick these off

- [ ] **5 areas** on the map, each looking different: a desk with a monitor, workbenches, a bar with tables, a bed, and a second office. Their names are readable.
- [ ] **5 little characters**, each with a different outfit or accessory and a name underneath. Mabel has a grey dot by her name, and Dina has a half-filled dot.
- [ ] The **clock** in the side panel goes 🌅 Morning → ☀️ Afternoon → 🌙 Night → next Day.
- [ ] Characters **walk** (not jump) between places when the time changes. At Night, **Charlie walks to the tavern**.
- [ ] **Speech bubbles** pop up over whoever is talking and disappear after about 6 seconds. The chat log on the right fills up, and the grey *italic* lines are system messages.
- [ ] Charlie's **cash** and **sobriety** change in his character card.
- [ ] The map gets a **dark blue tint at Night**.
- [ ] Make the browser window narrow (phone size): the side panel moves **under** the map.
- [ ] **Admin:** open "Admin" at the bottom of the panel and type `test`. Pause stops the clock and Resume starts it again. With an empty or wrong password, you get an error message.

### 1e. Stop the server

Go back to the terminal and press **Ctrl + C**.

✅ If everything was ticked, Gate C is done.
❌ If something looked wrong, write down which item failed and ask Claude to fix it.

---

## Step 2 — Decide one small thing: the page title

The browser tab currently always says **"Pixel Town"**. The text is typed directly into `server/static/index.html` (line 7).
The spec says world names shouldn't be hardcoded, so this is a borderline case. Pick one:

- **A — Leave it as is.** Nothing to do.
- **B — Make the tab show the current world's name.** Ask Claude: *"make the page title use the world name from /definition"*. It's a one-line change.

---

## Step 3 — Save your work in git (commit)

Only Phase A is committed so far. Phases B, C and D are not.

### 3a. See what's waiting

```bash
git status
```

You should see:
- `engine/simulation.py` (modified)
- `server/`, `Dockerfile`, `.dockerignore`, `README.md` and `NEXT_STEPS.md` (new)

`runtime/` and `.venv/` should **not** show up, because they're ignored on purpose.

### 3b. Commit everything at once

```bash
git add engine/simulation.py server/ Dockerfile .dockerignore README.md NEXT_STEPS.md
git commit -m "Add web server, pixel renderer, Docker and README (phases B-D)"
```

> `NEXT_STEPS.md` isn't part of the spec's file list. If you'd rather not keep it in the project, leave it out of the
> `git add` line and delete it once you're done.

---

## Step 4 — Try real AI characters (Gemini) once

Everything so far used fake characters. This step uses real Gemini, which **costs money** (very little).

### 4a. What you need

- A Gemini API key from **https://aistudio.google.com/apikey**.

### 4b. Put the key into this terminal only

Never put it in a file, and never paste it into a chat.

```bash
export GEMINI_API_KEY="paste-your-key-here"
```

The key only lasts until you close this terminal window.

### 4c. Start with a small safety limit

```bash
LLM_MODE=gemini MAX_LLM_CALLS_PER_HOUR=30 ALWAYS_RUN=false ADMIN_TOKEN=test .venv/bin/uvicorn server.app:app --port 8000
```

- `MAX_LLM_CALLS_PER_HOUR=30` sets a hard cap, so you can't overspend by accident.
- `ALWAYS_RUN=false` means the town only runs while the page is open. Close the tab and it stops calling Gemini.
- One in-game day costs about **9–12 Gemini calls**.

Open **http://localhost:8000** and watch for a minute or two. Characters should now say **real, varied lines** instead of `[mock] …`.

### 4d. If characters stay silent

Look at the terminal. If you see lines like:

```
[llm] charlie: attempt failed: ... response_schema ...
```

then the Gemini library doesn't like one setting. The spec's fix is to ask Claude: *"remove only the response_schema line in engine/llm.py, as the spec's Known SDK risk says"*.

### 4e. Stop

Press **Ctrl + C**. To also forget the key, close the terminal window.

---

## Step 5 — Install Docker and test the container (Gate D)

Docker wasn't installed, so this check was skipped. You only need it if you plan to put the town online (Step 6).

### 5a. Install Docker

Download **Docker Desktop for Mac** from **https://www.docker.com/products/docker-desktop/**, install it and open it.
Wait until the whale icon in the menu bar stops animating.

Check it works:

```bash
docker --version
```

### 5b. Build the image

```bash
docker build -t pixel-town .
```

It should end without red errors. The first build takes a few minutes.

### 5c. Run it (mock mode, free)

```bash
docker run --rm -p 8000:8000 -e LLM_MODE=mock -e ALWAYS_RUN=true -e ADMIN_TOKEN=test -v pixel-town-runtime:/app/runtime pixel-town
```

In a **second** terminal:

```bash
curl localhost:8000/healthz
```

It should print `{"ok":true}`. You can also open http://localhost:8000 in the browser.

### 5d. Check that the town remembers where it was

1. Note the **Day** shown in the browser.
2. Press **Ctrl + C** in the Docker terminal, then run the same `docker run …` command again.
3. Refresh the browser. It should continue from the **same Day**, not restart at Day 1.

The `-v pixel-town-runtime:/app/runtime` part is what keeps the save file.

✅ Gate D done.

---

## Step 6 — Put it online (optional)

Only do this if you want other people to watch your town.

### Rules (from the spec)

- Use a host that runs a **container all the time** and has a **persistent disk**, such as **Render**, **Fly.io** or **Railway**.
- Do **not** use Vercel, Netlify functions, Lambda or Cloud Functions. They can't run a long-lived server.
- Run exactly **one** instance. The town lives in memory.

### What to set on the host

| Setting | Value |
|---|---|
| Build from | this repo's `Dockerfile` |
| Persistent disk / volume | mounted at **`/app/runtime`** (or the town resets on every deploy) |
| Health check path | **`/healthz`** |
| Secret `GEMINI_API_KEY` | your Gemini key |
| Secret `ADMIN_TOKEN` | a long random password, not `test` (make one with `openssl rand -hex 16`) |
| `MAX_LLM_CALLS_PER_HOUR` | start low, e.g. `60` |
| `ALWAYS_RUN` | leave as `false` unless you accept non-stop Gemini costs |

The host gives you an `https://…` address, and that's your town's public link.
Your API key stays on the server, and visitors never see it.

---

## Quick reference

All of these assume you already ran `cd ~/Documents/personal/practice/pixel-town`.

| I want to… | Type this |
|---|---|
| Check the world files are valid | `.venv/bin/python -m engine.validate` |
| Run the tests | `.venv/bin/pytest -q` |
| Run the town for free (fake characters) | `LLM_MODE=mock TIME_SCALE=1.0 ALWAYS_RUN=true ADMIN_TOKEN=test .venv/bin/uvicorn server.app:app --port 8000` |
| Run with real AI | `export GEMINI_API_KEY="…"` then `LLM_MODE=gemini MAX_LLM_CALLS_PER_HOUR=30 ADMIN_TOKEN=test .venv/bin/uvicorn server.app:app --port 8000` |
| Restart the town from Day 1 | stop the server, then `rm -rf runtime/pixel-town` |
| Stop the server | **Ctrl + C** |
| Add a character, location or rule | see the **Authoring guide** in `README.md`, then run `.venv/bin/python -m engine.validate` |

---

## Where errors are recorded

While the server runs, problems are written to **`runtime/server.log`** as well as the terminal. The file stays after you quit, so you can read it later and ask Claude to fix what it shows.

What goes in the file (each line has a date and time):
- Server start and stop: `started: …`, `shutting down`, `saved state for pixel-town`.
- A time slot that crashed, with the full error trace. The town keeps going after 5 seconds.
- A broken rule in `rules.json` (`[rules] …`).
- Bad world files found at start-up (`ERROR: …`), and a missing Gemini key.
- uvicorn's own errors, such as a request that crashed.

**Not** in the file: failed Gemini calls (`[llm] … attempt failed`). Those still show only in the terminal, because the spec says `engine/llm.py` must stay word for word.

To find the problems later:

```bash
grep -n -E "ERROR|WARNING|Traceback" runtime/server.log
```

To read the whole file, run `less runtime/server.log` (press **q** to quit).
When the file reaches about 1 MB, it's renamed to `server.log.1` and a new one starts. Only the 3 newest old files are kept.
The file is gitignored, like the rest of `runtime/`, so it never gets committed.

---

## Final checklist

- [ ] Step 1 — Browser check passed (Gate C)
- [ ] Step 2 — Title decision made
- [ ] Step 3 — Phases B–D committed
- [ ] Step 4 — Real Gemini mode tried once
- [ ] Step 5 — Docker build and run worked (Gate D)
- [ ] Step 6 — (Optional) Deployed online
