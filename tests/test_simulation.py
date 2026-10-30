import asyncio
import json
import shutil

from conftest import WORLD_DIR, make_sim
from engine.loader import load_world


def run_slots(sim, n):
    async def go():
        for _ in range(n):
            await sim.run_slot()
    asyncio.run(go())


def stats(sim, cid):
    return sim.state["characters"][cid]["stats"]


def test_day1_no_actions(sim_no_plans):
    run_slots(sim_no_plans, 3)
    assert stats(sim_no_plans, "charlie")["cash"] == 10
    assert stats(sim_no_plans, "bob")["cash"] == 75
    assert sim_no_plans.state["globals"]["town_treasury"] == 180
    assert stats(sim_no_plans, "charlie")["sobriety"] == "Sober"
    assert sim_no_plans.state["clock"] == {"day": 2, "slot_index": 0}


def test_lend_then_drink(sim):
    run_slots(sim, 3)
    assert stats(sim, "charlie")["cash"] == 5
    assert stats(sim, "charlie")["sobriety"] == "Hungover"
    assert stats(sim, "bob")["cash"] == 65
    assert sim.state["globals"]["town_treasury"] == 180

    run_slots(sim, 3)
    assert stats(sim, "charlie")["cash"] == 10
    assert stats(sim, "charlie")["sobriety"] == "Sober"
    assert stats(sim, "bob")["cash"] == 80
    assert sim.state["globals"]["town_treasury"] == 160
    assert any("rejected for being broke" in e["text"] for e in sim.state["log"])


def test_hybrid_trigger(sim_no_plans):
    # sim_no_plans: with seed plans Charlie walks to the tavern at Night, which correctly wakes Dina there.
    run_slots(sim_no_plans, 3)
    calls = sim_no_plans.client.calls
    assert ("dina", "Afternoon") in calls
    assert ("dina", "Morning") not in calls
    assert ("dina", "Night") not in calls
    assert not any(cid == "mabel" for cid, _ in calls)


def mabel_lines(world, rules, characters, tmp_path, seed):
    sim = make_sim(world.model_copy(update={"seed": seed}), rules, characters, tmp_path / str(seed))
    run_slots(sim, 6)
    return [(e["day"], e["slot"], e["text"]) for e in sim.state["log"] if e["speaker"] == "mabel"]


def test_scripted_determinism(world, tmp_path):
    w, rules, characters = world
    first = mabel_lines(w, rules, characters, tmp_path / "a", 42)
    second = mabel_lines(w, rules, characters, tmp_path / "b", 42)
    other = mabel_lines(w, rules, characters, tmp_path / "c", 7)
    assert first == second
    assert first != other


def test_move_capacity(world, tmp_path):
    w, rules, characters = world
    chars = dict(characters)
    mabel = characters["mabel"]
    desk = {slot: "security_desk" for slot in w.clock.slots}
    for i in range(5):  # greg + 5 clones fill security_desk (capacity 3x2 = 6)
        cid = f"guard{i}"
        chars[cid] = mabel.model_copy(update={"id": cid, "name": cid, "home": "security_desk", "schedule": desk})
    chars["charlie"] = characters["charlie"].model_copy(
        update={"mock_plan": {"Morning": {"action": "none", "move_to": "security_desk"}}}
    )
    sim = make_sim(w, rules, chars, tmp_path)
    run_slots(sim, 1)
    assert sim.state["characters"]["charlie"]["location"] == "engineering_benches"
    assert sum(1 for cs in sim.state["characters"].values() if cs["location"] == "security_desk") == 6


def test_budget(world, tmp_path):
    sim = make_sim(*world, tmp_path, max_calls=1)
    run_slots(sim, 1)
    assert len(sim.client.calls) == 1


def test_resume(world, tmp_path):
    sim = make_sim(*world, tmp_path)
    run_slots(sim, 1)
    saved = json.loads((tmp_path / "pixel-town" / "state.json").read_text())
    resumed = make_sim(*world, tmp_path)
    assert resumed.state["clock"] == saved["clock"] == {"day": 1, "slot_index": 1}
    for cid in sim.state["characters"]:
        assert resumed.state["characters"][cid]["stats"] == sim.state["characters"][cid]["stats"]


def test_reconcile_state(tmp_path):
    world_dir = tmp_path / "world"
    shutil.copytree(WORLD_DIR, world_dir)
    runtime = tmp_path / "runtime"
    sim = make_sim(*load_world(world_dir), runtime)
    run_slots(sim, 3)
    before = {cid: dict(cs["stats"]) for cid, cs in sim.state["characters"].items()}

    newcomer = json.loads((world_dir / "characters" / "mabel.json").read_text())
    newcomer.update({"id": "nell", "name": "Nell", "stats": {"cash": 3}})
    (world_dir / "characters" / "nell.json").write_text(json.dumps(newcomer))

    reloaded = make_sim(*load_world(world_dir), runtime)
    assert reloaded.state["characters"]["nell"] == {"location": "local_tavern", "stats": {"cash": 3}}
    for cid, s in before.items():
        assert reloaded.state["characters"][cid]["stats"] == s
