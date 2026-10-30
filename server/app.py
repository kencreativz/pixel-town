import asyncio
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
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
)
RUNTIME_DIR = os.environ.get("RUNTIME_DIR", "runtime")

# Startup checks run at import so a failure exits the process with code 1.
_failed = False
for _folder in sorted(p for p in WORLDS_DIR.iterdir() if p.is_dir()):
    _errors, _warnings, _ = validate_world(_folder)
    for _w in _warnings:
        print(f"WARNING: {_folder.name}: {_w}")
    for _e in _errors:
        print(f"ERROR: {_folder.name}: {_e}")
    _failed = _failed or bool(_errors)
if _failed:
    sys.exit(1)
if LLM_MODE == "gemini" and not os.environ.get("GEMINI_API_KEY"):
    print("ERROR: GEMINI_API_KEY environment variable is not set.")
    sys.exit(1)

_client = make_client(LLM_MODE)
SIMS = {
    wid: Simulation(world, rules, characters, _client, RUNTIME_DIR, SETTINGS, Hub())
    for wid, (world, rules, characters) in load_all_worlds(WORLDS_DIR).items()
}


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(sim.run_forever()) for sim in SIMS.values()]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sim in SIMS.values():
            sim.save()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


def get_sim(world_id):
    sim = SIMS.get(world_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Unknown world.")
    return sim


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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
            {"id": c.id, "name": c.name, "kind": c.kind, "sprite": c.sprite.model_dump(exclude_none=True)}
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
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {payload}\n\n"
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
