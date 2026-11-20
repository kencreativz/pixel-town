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


def test_sum_value():
    ctx = make_ctx()
    assert resolve_value({"sum": ["$amount", "@actor.cash", 3]}, ctx) == 30
    assert eval_conditions([{"path": "actor.cash", "op": "gte", "value": {"sum": ["$amount", 13]}}], ctx)
    assert not eval_conditions([{"path": "actor.cash", "op": "gte", "value": {"sum": ["$amount", 14]}}], ctx)
    with pytest.raises(DSLError):
        resolve_value({"sum": ["$amount", "Wasted"]}, ctx)


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


def test_nested_debt_path():
    ctx = make_ctx()
    ctx.state["characters"]["b"]["stats"]["owes"] = {}
    assert resolve_value("@target.owes.actor", ctx) == 0  # missing entry reads as 0
    state, _ = apply_effects([{"add": {"path": "target.owes.actor", "amount": "$amount"}}], ctx)
    assert state["characters"]["b"]["stats"]["owes"] == {"a": 7}
    ctx2 = make_ctx(state=state)
    state, _ = apply_effects([{"sub": {"path": "target.owes.actor", "amount": 7}}], ctx2)
    assert state["characters"]["b"]["stats"]["owes"] == {}  # paid off entries are removed
    with pytest.raises(DSLError):  # 'a' has no owes stat
        apply_effects([{"add": {"path": "actor.owes.target", "amount": 1}}], ctx)


def test_sub_effect():
    state, _ = apply_effects([{"sub": {"path": "actor.cash", "amount": "$amount"}}], make_ctx())
    assert state["characters"]["a"]["stats"]["cash"] == 13


def test_remember_effect():
    ctx = make_ctx()
    ctx.state["clock"] = {"day": 3, "slot_index": 0}
    state, logs = apply_effects([{"remember": {"who": "target", "text": "{actor} lent me ${amount}."}}], ctx)
    assert state["characters"]["b"]["memory"] == ["Day 3: Alice lent me $7."]
    assert logs == []
    state, _ = apply_effects([{"remember": {"who": "actor", "text": "note"}}] * 12, ctx)
    assert len(state["characters"]["a"]["memory"]) == 10
    with pytest.raises(DSLError):
        apply_effects([{"remember": {"who": "char:nobody", "text": "hi"}}], ctx)


def test_say_effect():
    ctx = make_ctx()
    _, logs = apply_effects([{"say": {"who": "char:b", "text": "Here's ${amount}, {actor}."}}], ctx)
    assert logs == [("b", "Here's $7, Alice.")]
    with pytest.raises(DSLError):
        apply_effects([{"say": {"who": "char:nobody", "text": "hi"}}], ctx)


def test_max_value():
    ctx = make_ctx()
    assert resolve_value({"max": [0, {"sum": ["@target.cash", -20]}]}, ctx) == 0
    assert resolve_value({"max": [0, {"sum": ["@actor.cash", -5]}]}, ctx) == 15
    with pytest.raises(DSLError):
        resolve_value({"max": [0, "@actor.mood"]}, ctx)


def test_min_value():
    ctx = make_ctx()
    assert resolve_value({"min": [100, {"sum": ["@actor.cash", 15]}]}, ctx) == 35
    assert resolve_value({"min": [10, "@actor.cash"]}, ctx) == 10


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


def test_in_op():
    ctx = make_ctx()
    assert eval_conditions([{"path": "actor.location", "op": "in", "value": ["work", "home"]}], ctx)
    assert not eval_conditions([{"path": "actor.location", "op": "in", "value": ["nowhere"]}], ctx)
