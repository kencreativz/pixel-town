"""Generic rule interpreter: value references, paths, conditions and effects (spec A3)."""
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


MEMORY_KEEP = 10  # memory notes kept per character


class DSLError(Exception):
    pass


@dataclass
class Context:
    state: dict
    slot: str
    names: Dict[str, str]  # character id -> display name
    actor: Optional[str] = None
    target: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


def _who(name, ctx):
    """Resolve 'actor' / 'target' / 'char:<id>' to a character id (or None)."""
    if name == "actor":
        return ctx.actor
    if name == "target":
        return ctx.target
    if name.startswith("char:"):
        return name[len("char:"):]
    return None


def _split(path, ctx):
    """Return (container_dict, key, is_location, nested) for a path.
    A 3-part character path like 'target.owes.actor' points into a dict stat, keyed by the resolved character id."""
    head, sep, key = path.partition(".")
    if not sep or not key:
        raise DSLError(f"bad path: {path}")
    if head == "global":
        return ctx.state["globals"], key, False, False
    if head not in ("actor", "target") and not head.startswith("char:"):
        raise DSLError(f"bad path: {path}")
    cid = _who(head, ctx)
    if cid is None or cid not in ctx.state["characters"]:
        raise DSLError(f"no character for path: {path}")
    char_state = ctx.state["characters"][cid]
    key, sep, sub = key.partition(".")
    if sep:
        stat = char_state["stats"].get(key)
        sub_id = _who(sub, ctx)
        if not isinstance(stat, dict) or sub_id is None:
            raise DSLError(f"bad path: {path}")
        return stat, sub_id, False, True
    if key == "location":
        return char_state, key, True, False
    return char_state["stats"], key, False, False


def read_path(path, ctx):
    container, key, _, nested = _split(path, ctx)
    if nested:
        return container.get(key, 0)
    if key not in container:
        raise DSLError(f"missing path: {path}")
    return container[key]


def write_path(path, value, ctx):
    container, key, is_location, nested = _split(path, ctx)
    if is_location:
        raise DSLError(f"location is not writable: {path}")
    if nested:
        if value == 0:
            container.pop(key, None)
        else:
            container[key] = value
        return
    if key not in container:
        raise DSLError(f"missing path: {path}")
    container[key] = value


def resolve_value(value, ctx):
    if isinstance(value, dict) and "sum" in value:  # e.g. {"sum": ["$amount", "@global.vault_floor"]}
        return sum(_number(resolve_value(v, ctx)) for v in value["sum"])
    if isinstance(value, dict) and "max" in value:  # e.g. {"max": [0, {"sum": ["@target.health", -20]}]}
        return max(_number(resolve_value(v, ctx)) for v in value["max"])
    if isinstance(value, dict) and "min" in value:  # e.g. {"min": [100, {"sum": ["@actor.health", 15]}]}
        return min(_number(resolve_value(v, ctx)) for v in value["min"])
    if isinstance(value, str):
        if value.startswith("$"):
            name = value[1:]
            if name not in ctx.params:
                raise DSLError(f"unknown param: {value}")
            return ctx.params[name]
        if value.startswith("@"):
            return read_path(value[1:], ctx)
    return value


_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
}


def eval_condition(cond, ctx):
    try:
        if "slot_in" in cond:
            return ctx.slot in cond["slot_in"]
        try:
            left = read_path(cond["path"], ctx)
        except DSLError:  # a stat the character doesn't have: use the condition's "default", else fail
            if "default" not in cond:
                raise
            left = cond["default"]
        right = resolve_value(cond["value"], ctx)
        return bool(_OPS[cond["op"]](left, right))
    except Exception:
        return False


def eval_conditions(conds, ctx):
    return all(eval_condition(c, ctx) for c in (conds or []))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DSLError(f"not a number: {value!r}")
    return value


def format_log(text, ctx):
    names = ctx.names
    return (
        text.replace("{actor}", names.get(ctx.actor, str(ctx.actor)))
        .replace("{target}", names.get(ctx.target, str(ctx.target)))
        .replace("{amount}", str(ctx.params.get("amount")))
    )


def _apply_one(effect, ctx, logs):
    if "add" in effect:
        spec = effect["add"]
        current = _number(read_path(spec["path"], ctx))
        write_path(spec["path"], current + _number(resolve_value(spec["amount"], ctx)), ctx)
    elif "sub" in effect:
        spec = effect["sub"]
        current = _number(read_path(spec["path"], ctx))
        write_path(spec["path"], current - _number(resolve_value(spec["amount"], ctx)), ctx)
    elif "set" in effect:
        spec = effect["set"]
        read_path(spec["path"], ctx)
        write_path(spec["path"], resolve_value(spec["value"], ctx), ctx)
    elif "transfer" in effect:
        spec = effect["transfer"]
        amount = _number(resolve_value(spec["amount"], ctx))
        src = _number(read_path(spec["from"], ctx))
        dst = _number(read_path(spec["to"], ctx))
        write_path(spec["from"], src - amount, ctx)
        write_path(spec["to"], dst + amount, ctx)
    elif "move" in effect:
        spec = effect["move"]
        cid = ctx.actor if spec["who"] == "actor" else ctx.target if spec["who"] == "target" else None
        if cid is None or cid not in ctx.state["characters"]:
            raise DSLError(f"bad move.who: {spec['who']}")
        ctx.state["characters"][cid]["location"] = spec["to"]
    elif "log" in effect:
        logs.append(format_log(effect["log"], ctx))
    elif "say" in effect:
        spec = effect["say"]
        who = spec["who"]
        cid = ctx.actor if who == "actor" else ctx.target if who == "target" else who[len("char:"):] if who.startswith("char:") else None
        if cid is None or cid not in ctx.state["characters"]:
            raise DSLError(f"bad say.who: {who}")
        logs.append((cid, format_log(spec["text"], ctx)))
    elif "remember" in effect:
        spec = effect["remember"]
        cid = _who(spec["who"], ctx)
        if cid is None or cid not in ctx.state["characters"]:
            raise DSLError(f"bad remember.who: {spec['who']}")
        day = ctx.state.get("clock", {}).get("day", 1)
        notes = ctx.state["characters"][cid].setdefault("memory", [])
        notes.append(f"Day {day}: {format_log(spec['text'], ctx)}")
        del notes[:-MEMORY_KEEP]
    else:
        raise DSLError(f"unknown effect: {effect}")


def apply_effects(effects, ctx):
    """Apply effects to a working copy. Returns (new_state, logs); each log is a system line (str) or a speech (cid, text). Raises DSLError on any failure."""
    work = copy.deepcopy(ctx.state)
    wctx = Context(state=work, slot=ctx.slot, names=ctx.names, actor=ctx.actor, target=ctx.target, params=ctx.params)
    logs = []
    try:
        for effect in effects:
            _apply_one(effect, wctx, logs)
    except DSLError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:  # malformed effect spec
        raise DSLError(f"effect failed: {exc!r}") from exc
    return work, logs
