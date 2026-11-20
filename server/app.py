import asyncio
import json
import logging
import os
import secrets
import signal
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.hub import Hub
from engine.llm import make_client
from engine.loader import load_all_worlds
from engine.simulation import Settings, Simulation
from engine.validate import WORLDS_DIR, validate_world

STATIC_DIR = Path(__file__).resolve().parent / "static"
KEEPALIVE_SECONDS = 15

LLM_MODE = os.environ.get("LLM_MODE", "gemini")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or None
SETTINGS = Settings(
    time_scale=float(os.environ.get("TIME_SCALE", "1.0")),
    always_run=os.environ.get("ALWAYS_RUN", "false").lower() == "true",
    max_calls_per_hour=int(os.environ.get("MAX_LLM_CALLS_PER_HOUR", "600")),
    all_llm=LLM_MODE == "ollama",
    guard_turns=LLM_MODE in ("ollama", "gemini"),
)
RUNTIME_DIR = os.environ.get("RUNTIME_DIR", "runtime")

# Errors, warnings and start/stop go to the terminal and to runtime/server.log (kept after quitting).
# uvicorn sets up its own logging before importing this module, so these handlers stay in place.
Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(
    Path(RUNTIME_DIR) / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
)
logger = logging.getLogger("pixel_town")
logger.setLevel(logging.INFO)
logger.addHandler(_file_handler)
logger.addHandler(logging.StreamHandler())
logging.getLogger("uvicorn").addHandler(_file_handler)

# Startup checks run at import so a failure exits the process with code 1.
_failed = False
for _folder in sorted(p for p in WORLDS_DIR.iterdir() if p.is_dir()):
    _errors, _warnings, _ = validate_world(_folder)
    for _w in _warnings:
        logger.warning("WARNING: %s: %s", _folder.name, _w)
    for _e in _errors:
        logger.error("ERROR: %s: %s", _folder.name, _e)
    _failed = _failed or bool(_errors)
if _failed:
    sys.exit(1)
if LLM_MODE == "gemini" and not os.environ.get("GEMINI_API_KEY"):
    logger.error("ERROR: GEMINI_API_KEY environment variable is not set.")
    sys.exit(1)

_client = make_client(LLM_MODE)
SIMS = {
    wid: Simulation(world, rules, characters, _client, RUNTIME_DIR, SETTINGS, Hub())
    for wid, (world, rules, characters) in load_all_worlds(WORLDS_DIR).items()
}


# Set on Ctrl + C / SIGTERM so the open live streams end; otherwise uvicorn waits for the browser to disconnect.
_stopping = asyncio.Event()


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(sim.run_forever()) for sim in SIMS.values()]
    # Runs inside uvicorn's signal capture, so the previous handler is uvicorn's own; call it after ours.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if callable(previous):

            def on_signal(signum, frame, previous=previous):
                loop.call_soon_threadsafe(_stopping.set)
                previous(signum, frame)

            signal.signal(sig, on_signal)
    logger.info("started: worlds=%s mode=%s", ", ".join(SIMS), LLM_MODE)
    try:
        yield
    finally:
        logger.info("shutting down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for wid, sim in SIMS.items():
            sim.save()
            logger.info("saved state for %s", wid)


class NoCacheStaticFiles(StaticFiles):
    """Browsers re-check each file with the server, so edited JS/CSS loads on a normal reload."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(lifespan=lifespan)
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


def get_sim(world_id):
    sim = SIMS.get(world_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Unknown world.")
    return sim


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/worlds")
def list_worlds():
    return [{"id": wid, "name": sim.world.name} for wid, sim in SIMS.items()]


@app.get("/api/worlds/{world_id}/definition")
def definition(world_id: str):
    sim = get_sim(world_id)
    world = sim.world
    return {
        "id": world.id,
        "name": world.name,
        "map": world.map.model_dump(),
        "slots": world.clock.slots,
        "locations": {
            lid: {"name": loc.name, "type": loc.type, "color": loc.color, "rect": loc.rect.model_dump()}
            for lid, loc in world.locations.items()
        },
        "characters": [
            {"id": c.id, "name": c.name, "kind": c.kind, "guard": c.guard, "home": c.home,
             "sprite": c.sprite.model_dump(exclude_none=True)}
            for c in (sim.characters[cid] for cid in sim.order)
        ],
        "turn_order": sim.order,
    }


@app.get("/api/worlds/{world_id}/snapshot")
def snapshot(world_id: str):
    return get_sim(world_id).snapshot()


@app.get("/api/worlds/{world_id}/stream")
async def stream(world_id: str):
    sim = get_sim(world_id)

    async def events():
        queue = sim.hub.subscribe()
        try:
            yield f"data: {json.dumps(sim.snapshot())}\n\n"
            while not _stopping.is_set():
                getter = asyncio.ensure_future(queue.get())
                stopper = asyncio.ensure_future(_stopping.wait())
                done, pending = await asyncio.wait(
                    {getter, stopper}, timeout=KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if getter in done:
                    yield f"data: {getter.result()}\n\n"
                elif not done:
                    yield ": keepalive\n\n"
        finally:
            sim.hub.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ControlBody(BaseModel):
    action: Literal["pause", "resume", "reset"]


@app.post("/api/worlds/{world_id}/control")
def control(world_id: str, body: ControlBody, x_admin_token: Optional[str] = Header(default=None)):
    sim = get_sim(world_id)
    if ADMIN_TOKEN is None or not secrets.compare_digest((x_admin_token or "").encode(), ADMIN_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Forbidden: invalid or missing admin token.")
    if body.action == "pause":
        sim.pause()
    elif body.action == "resume":
        sim.resume()
    else:
        sim.request_reset()
    return {"ok": True}
