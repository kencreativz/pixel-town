import copy

import pytest

from engine.dsl import Context, DSLError, apply_effects, eval_conditions, resolve_value


def make_ctx(**kw):
    state = {
        "globals": {"town_treasury": 100, "threat_level": "Peaceful"},
        "characters": {
            "a": {"location": "home", "stats": {"cash": 20, "mood": "ok"}},
            "b": {"location": "work", "stats": {"cash": 5}},
        },
    }
    defaults = dict(state=state, slot="Night", names={"a": "Alice", "b": "Ben"}, actor="a", target="b",
                    params={"amount": 7, "target": "b"})
    defaults.update(kw)
    return Context(**defaults)


@pytest.mark.parametrize("op,value,expected", [
    ("eq", 20, True), ("eq", 21, False),
    ("ne", 21, True), ("ne", 20, False),
    ("gt", 19, True), ("gt", 20, False),
    ("gte", 20, True), ("gte", 21, False),
    ("lt", 21, True), ("lt", 20, False),
    ("lte", 20, True), ("lte", 19, False),
])
def test_condition_ops(op, value, expected):
    assert eval_conditions([{"path": "actor.cash", "op": op, "value": value}], make_ctx()) is expected


def test_slot_in_and_missing_path():
    ctx = make_ctx()
    assert eval_conditions([{"slot_in": ["Night"]}], ctx)
    assert not eval_conditions([{"slot_in": ["Morning"]}], ctx)
    assert not eval_conditions([{"path": "actor.nope", "op": "eq", "value": 1}], ctx)
    assert eval_conditions([], ctx)


def test_value_refs():
    ctx = make_ctx()
    assert resolve_value(3, ctx) == 3
    assert resolve_value("Wasted", ctx) == "Wasted"
    assert resolve_value("$amount", ctx) == 7
    assert resolve_value("$target", ctx) == "b"
    assert resolve_value("@actor.cash", ctx) == 20
    assert resolve_value("@target.location", ctx) == "work"
    assert resolve_value("@char:b.cash", ctx) == 5
    assert resolve_value("@global.town_treasury", ctx) == 100
    assert eval_conditions([{"path": "actor.cash", "op": "gte", "value": "$amount"}], ctx)


def test_effects():
    ctx = make_ctx()
    state, logs = apply_effects([
        {"add": {"path": "actor.cash", "amount": -3}},
        {"set": {"path": "actor.mood", "value": "great"}},
        {"transfer": {"from": "global.town_treasury", "to": "target.cash", "amount": "$amount"}},
        {"move": {"who": "target", "to": "tavern"}},
        {"log": "{actor} gave {target} ${amount}."},
    ], ctx)
    assert state["characters"]["a"]["stats"] == {"cash": 17, "mood": "great"}
    assert state["characters"]["b"]["stats"]["cash"] == 12
    assert state["globals"]["town_treasury"] == 93
    assert state["characters"]["b"]["location"] == "tavern"
    assert logs == ["Alice gave Ben $7."]


def test_atomic_rollback():
    ctx = make_ctx()
    before = copy.deepcopy(ctx.state)
    with pytest.raises(DSLError):
        apply_effects([
            {"add": {"path": "actor.cash", "amount": 5}},
            {"add": {"path": "actor.missing", "amount": 1}},
        ], ctx)
    assert ctx.state == before


def test_location_write_rejected():
    ctx = make_ctx()
    with pytest.raises(DSLError):
        apply_effects([{"set": {"path": "actor.location", "value": "tavern"}}], ctx)
    assert ctx.state["characters"]["a"]["location"] == "home"
