"""CLI: python -m engine.validate [world_id ...]"""
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import Character, Rules, World

WORLDS_DIR = Path("worlds")
KNOWN_STYLES = {"security_guard", "engineer_blue", "laborer_yellow", "shopkeeper", "landlord"}
MAX_LLM = 8


def _load(path, model, errors):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: invalid JSON: {exc}")
        return None, None
    try:
        return data, model.model_validate(data)
    except ValidationError as exc:
        errors.append(f"{path.name}: schema error: {exc}")
        return data, None


def _value_refs(value, where, params, char_ids, errors):
    if not isinstance(value, str):
        return
    if value.startswith("$"):
        if params is None or value[1:] not in params:
            errors.append(f"{where}: {value} is not a declared param")
    elif value.startswith("@"):
        _path_ref(value[1:], where, char_ids, errors)


def _path_ref(path, where, char_ids, errors, write=False):
    if not isinstance(path, str):
        errors.append(f"{where}: path must be a string")
        return
    head = path.split(".", 1)[0]
    if head.startswith("char:") and head[len("char:"):] not in char_ids:
        errors.append(f"{where}: unknown character in path '{path}'")
    if write and path.endswith(".location"):
        errors.append(f"{where}: cannot write '{path}' (use the move effect)")


def _check_conditions(conds, where, params, char_ids, errors):
    for cond in conds or []:
        if "path" in cond:
            _path_ref(cond["path"], where, char_ids, errors)
            _value_refs(cond.get("value"), where, params, char_ids, errors)


def _check_effects(effects, where, params, char_ids, location_ids, errors):
    for effect in effects:
        if "add" in effect:
            spec = effect["add"]
            _path_ref(spec.get("path"), where, char_ids, errors, write=True)
            _value_refs(spec.get("amount"), where, params, char_ids, errors)
        elif "set" in effect:
            spec = effect["set"]
            _path_ref(spec.get("path"), where, char_ids, errors, write=True)
            _value_refs(spec.get("value"), where, params, char_ids, errors)
        elif "transfer" in effect:
            spec = effect["transfer"]
            _path_ref(spec.get("from"), where, char_ids, errors, write=True)
            _path_ref(spec.get("to"), where, char_ids, errors, write=True)
            _value_refs(spec.get("amount"), where, params, char_ids, errors)
        elif "move" in effect:
            if effect["move"].get("to") not in location_ids:
                errors.append(f"{where}: move.to '{effect['move'].get('to')}' is not a known location")


def _overlap(a, b):
    return a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.height and b.y < a.y + a.height


def validate_world(path):
    """Returns (errors, warnings, summary). summary is the OK line, or None on error."""
    path = Path(path)
    errors, warnings = [], []
    _, world = _load(path / "world.json", World, errors)
    _, rules = _load(path / "rules.json", Rules, errors)

    characters = []
    seen = set()
    for file in sorted((path / "characters").glob("*.json")):
        data, char = _load(file, Character, errors)
        cid = data.get("id") if isinstance(data, dict) else None
        if cid is not None:
            if file.stem != cid:
                errors.append(f"{file.name}: filename does not match id '{cid}'")
            if cid in seen:
                errors.append(f"{file.name}: duplicate character id '{cid}'")
            seen.add(cid)
        if char is not None:
            characters.append(char)

    if world is None or rules is None:
        return errors, warnings, None

    slots = world.clock.slots
    location_ids = set(world.locations)
    char_ids = {c.id for c in characters}

    for cid in world.turn_order:
        if cid not in char_ids:
            errors.append(f"world.json: turn_order contains unknown id '{cid}'")

    for lid, loc in world.locations.items():
        r = loc.rect
        if r.x < 0 or r.y < 0 or r.x + r.width > world.map.width or r.y + r.height > world.map.height:
            errors.append(f"world.json: location '{lid}' rect is outside the map")
    items = list(world.locations.items())
    for i, (aid, a) in enumerate(items):
        for bid, b in items[i + 1:]:
            if _overlap(a.rect, b.rect):
                errors.append(f"world.json: locations '{aid}' and '{bid}' overlap")

    for c in characters:
        where = f"characters/{c.id}.json"
        if c.home not in location_ids:
            errors.append(f"{where}: home '{c.home}' is not a known location")
        for slot, lid in c.schedule.items():
            if slot not in slots:
                errors.append(f"{where}: schedule slot '{slot}' is not in clock.slots")
            if lid not in location_ids:
                errors.append(f"{where}: schedule location '{lid}' is not a known location")
        for aid in c.allowed_actions:
            if aid not in rules.actions:
                errors.append(f"{where}: allowed action '{aid}' is not in rules.actions")
        for stat in c.public_stats:
            if stat not in c.stats:
                errors.append(f"{where}: public stat '{stat}' is not in stats")
        if c.sprite.style is not None and c.sprite.style not in KNOWN_STYLES:
            warnings.append(f"{where}: unknown sprite.style '{c.sprite.style}'")
        if c.id not in world.turn_order:
            warnings.append(f"{where}: '{c.id}' is missing from turn_order (appended automatically)")

    for slot in slots:
        counts = {}
        for c in characters:
            lid = c.schedule.get(slot) or c.home
            counts[lid] = counts.get(lid, 0) + 1
        for lid, n in counts.items():
            if lid in world.locations:
                rect = world.locations[lid].rect
                if n > rect.width * rect.height:
                    errors.append(f"{slot}: {n} characters scheduled in '{lid}' (capacity {rect.width * rect.height})")

    for aid, action in rules.actions.items():
        where = f"rules.json: action '{aid}'"
        for slot in action.only_slots or []:
            if slot not in slots:
                errors.append(f"{where}: only_slots entry '{slot}' is not in clock.slots")
        _check_conditions(action.requires, where, action.params, char_ids, errors)
        _check_effects(action.effects, where, action.params, char_ids, location_ids, errors)

    for event in rules.events:
        where = f"rules.json: event '{event.id}'"
        for slot in event.when.slots or []:
            if slot not in slots:
                errors.append(f"{where}: when.slots entry '{slot}' is not in clock.slots")
        if not (event.for_each == "global" or (event.for_each.startswith("stat:") and len(event.for_each) > 5)):
            errors.append(f"{where}: for_each must be 'global' or 'stat:<name>'")
        for branch in event.branches:
            _check_conditions(branch.if_, where, None, char_ids, errors)
            _check_effects(branch.effects, where, None, char_ids, location_ids, errors)

    n_llm = sum(1 for c in characters if c.kind == "llm")
    n_hybrid = sum(1 for c in characters if c.kind == "hybrid")
    if n_llm > MAX_LLM:
        warnings.append(f"{n_llm} llm characters (more than {MAX_LLM})")

    if errors:
        return errors, warnings, None
    summary = (
        f"OK: {world.id} ({len(characters)} characters, {len(world.locations)} locations). "
        f"Estimated Gemini calls/day: {n_llm * len(slots)}-{(n_llm + n_hybrid) * len(slots)}"
    )
    return errors, warnings, summary


def main(argv):
    ids = argv or sorted(p.name for p in WORLDS_DIR.iterdir() if p.is_dir())
    failed = False
    for wid in ids:
        errors, warnings, summary = validate_world(WORLDS_DIR / wid)
        for w in warnings:
            print(f"WARNING: {wid}: {w}")
        for e in errors:
            print(f"ERROR: {wid}: {e}")
        if errors:
            failed = True
        else:
            print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
