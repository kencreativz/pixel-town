import copy
import json
from pathlib import Path

from .models import Character, Rules, World


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_world(path):
    """Load one world folder. Returns (world, rules, characters) with characters as {id: Character}."""
    path = Path(path)
    world = World.model_validate(_read_json(path / "world.json"))
    rules = Rules.model_validate(_read_json(path / "rules.json"))
    characters = {}
    for file in sorted((path / "characters").glob("*.json")):
        char = Character.model_validate(_read_json(file))
        characters[char.id] = char
    return world, rules, characters


def load_all_worlds(worlds_dir="worlds"):
    """Load every world folder. Returns {world_id: (world, rules, characters)}."""
    worlds = {}
    for folder in sorted(Path(worlds_dir).iterdir()):
        if folder.is_dir():
            world, rules, characters = load_world(folder)
            worlds[world.id] = (world, rules, characters)
    return worlds


def initial_character_state(world, char):
    first_slot = world.clock.slots[0]
    return {"location": char.schedule.get(first_slot) or char.home, "stats": copy.deepcopy(char.stats)}


def initial_state(world, characters):
    return {
        "world_id": world.id,
        "clock": {"day": 1, "slot_index": 0},
        "globals": copy.deepcopy(world.globals),
        "characters": {cid: initial_character_state(world, c) for cid, c in characters.items()},
        "log": [],
    }


def reconcile_state(state, world, characters):
    """Bring a saved state in line with the current world files without overwriting saved values."""
    saved = state.setdefault("characters", {})
    for cid in list(saved):
        if cid not in characters:
            del saved[cid]
    for cid, char in characters.items():
        if cid not in saved:
            saved[cid] = initial_character_state(world, char)
            continue
        stats = saved[cid].setdefault("stats", {})
        for key, value in char.stats.items():
            if key not in stats:
                stats[key] = copy.deepcopy(value)
    globals_ = state.setdefault("globals", {})
    for key, value in world.globals.items():
        if key not in globals_:
            globals_[key] = copy.deepcopy(value)
    return state
