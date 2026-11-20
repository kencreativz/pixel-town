import asyncio
import json
import shutil

from conftest import WORLD_DIR, make_sim
from engine.loader import load_world


JAIL_STATS = {"health": 100, "jailed": False, "wanted": False, "bail": 30, "hit_by": {}, "sorry": {}}
CARD_STATS = {"house": "Owned", "credit_card": False, "card_balance": 0, "card_payment": 10, "card_missed": 0, "hosts": {}}
LOAN_STATS = {"loan": 0, "loan_payment": 10, "missed_payments": 0}
BURGLARY_STATS = {"intruder": "", "burglaries": 0, "stolen": 0, "saw_burglary": {}, "burglar_wanted": False}


def run_slots(sim, n):
    async def go():
        for _ in range(n):
            await sim.run_slot()
    asyncio.run(go())


def stats(sim, cid):
    return sim.state["characters"][cid]["stats"]


def test_day1_no_actions(sim_no_plans):
    run_slots(sim_no_plans, 3)
    # Pay lands in the bank; Charlie's $105 head start covers the $20 rent.
    assert stats(sim_no_plans, "charlie")["cash"] == 85
    assert stats(sim_no_plans, "charlie")["bank"] == 52
    assert stats(sim_no_plans, "charlie")["rent_due"] == 20
    assert stats(sim_no_plans, "bob")["cash"] == 88
    assert stats(sim_no_plans, "bob")["bank"] == 88
    assert sim_no_plans.state["globals"]["town_treasury"] == 289  # $26 Dina dev pay included
    assert stats(sim_no_plans, "charlie")["sobriety"] == "Sober"
    assert sim_no_plans.state["clock"] == {"day": 2, "slot_index": 0}


def test_lend_then_drink(sim):
    stats(sim, "charlie")["cash"] = 15  # start broke (no head start) so the rent and drink refusals still happen
    run_slots(sim, 3)
    assert stats(sim, "charlie")["cash"] == 5
    assert stats(sim, "charlie")["sobriety"] == "Hungover"
    texts = [e["text"] for e in sim.state["log"]]
    wasted = next(i for i, t in enumerate(texts) if "got wasted" in t)
    assert texts.index("[mock] Just one drink. What could go wrong?") > wasted  # says it at the tavern, after drinking
    assert stats(sim, "bob")["cash"] == 103
    assert stats(sim, "charlie")["bank"] == 37
    assert stats(sim, "bob")["bank"] == 63
    assert sim.state["globals"]["town_treasury"] == 289
    assert any(e["speaker"] == "tess" and e["text"].startswith("Here's your $15, Charlie") for e in sim.state["log"])
    assert stats(sim, "charlie")["owes"] == {"bob": 10}
    assert "Day 1: Bob lent me $10. I owe them." in sim.state["characters"]["charlie"]["memory"]
    assert "Day 1: I lent Charlie $10." in sim.state["characters"]["bob"]["memory"]

    run_slots(sim, 3)
    assert stats(sim, "charlie")["cash"] == 10
    assert stats(sim, "charlie")["sobriety"] == "Sober"
    assert stats(sim, "bob")["cash"] == 118
    assert stats(sim, "charlie")["bank"] == 48
    assert stats(sim, "bob")["bank"] == 82
    assert sim.state["globals"]["town_treasury"] == 228
    assert any("rejected for being broke" in e["text"] for e in sim.state["log"])


def test_walk_seconds(sim):
    # benches centre (14.5, 5) -> bank centre (24, 4): 9.5 + 1 tiles, +4 for doors, at 0.5 s/tile
    assert sim._walk_seconds([("engineering_benches", "town_bank")]) == 7.25
    assert sim._walk_seconds([("town_bank", "town_bank")]) == 0


def test_atm_only_at_bank(world, tmp_path):
    w, rules, characters = world
    chars = dict(characters)
    chars["charlie"] = characters["charlie"].model_copy(
        update={"mock_plan": {"Afternoon": {"action": "atm_withdraw", "amount": 15}}}
    )
    sim = make_sim(w, rules, chars, tmp_path)
    run_slots(sim, 2)
    assert stats(sim, "charlie")["bank"] == 52
    assert stats(sim, "charlie")["cash"] == 115
    assert any("tried the ATM" in e["text"] for e in sim.state["log"])


def bank_sim(world, tmp_path, plans):
    """Seed world where the given characters' Afternoon plans are replaced (everyone else keeps theirs)."""
    w, rules, characters = world
    chars = dict(characters)
    for cid, plan in plans.items():
        chars[cid] = characters[cid].model_copy(update={"mock_plan": {"Afternoon": plan}})
    return make_sim(w, rules, chars, tmp_path)


def test_teller_hears_amount_first(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "withdraw", "amount": 15, "move_to": "town_bank"}})
    run_slots(sim, 2)
    lines = [(e["speaker"], e["text"]) for e in sim.state["log"]]
    ask = lines.index(("charlie", "Hi Tess, I'd like to withdraw $15, please."))
    reply = lines.index(("tess", "Here's your $15, Charlie. Have a good one."))
    assert ask < reply
    assert sim.state["characters"]["charlie"]["spot"] == "teller"


def test_atm_has_no_teller(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "atm_withdraw", "amount": 15, "move_to": "town_bank"}})
    run_slots(sim, 2)
    assert any("withdrew $15 from the ATM" in e["text"] for e in sim.state["log"])
    assert not any(e["speaker"] == "tess" and "$15" in e["text"] for e in sim.state["log"])
    # Bob's own plan uses the ATM after Charlie, so Charlie has stepped away and Bob holds it.
    assert sim.state["characters"]["charlie"]["spot"] is None
    assert sim.state["characters"]["bob"]["spot"] == "atm"


def test_one_at_the_atm(world, tmp_path):
    # Charlie (first in turn order) takes the ATM; Bob arrives later, sits and waits, then Charlie steps away.
    sim = bank_sim(world, tmp_path, {
        "charlie": {"action": "atm_withdraw", "amount": 15, "move_to": "town_bank"},
        "bob": {"action": "atm_withdraw", "amount": 25, "move_to": "town_bank"},
    })
    seen = []
    publish = sim.hub.publish

    def watch(event):
        if event["type"] == "state":
            cs = event["state"]["characters"]
            seen.append((cs["charlie"].get("spot"), cs["bob"].get("spot")))
        return publish(event)

    sim.hub.publish = watch
    run_slots(sim, 2)
    assert ("atm", "wait") in seen  # Bob waits while Charlie is at the ATM
    assert seen.index(("atm", "wait")) < seen.index((None, "atm"))  # then Charlie steps away and Bob steps up
    assert stats(sim, "bob")["bank"] == 63 and stats(sim, "charlie")["bank"] == 37


def test_spots_clear_at_slot_start(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "atm_withdraw", "amount": 15, "move_to": "town_bank"}})
    run_slots(sim, 3)
    assert all(cs.get("spot") is None for cs in sim.state["characters"].values())


def test_borrow_and_payments(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "borrow", "amount": 30, "move_to": "town_bank"}})
    run_slots(sim, 2)
    # Bob lent $10 in the Morning, the bank lent $30 (+$5 fee) in the Afternoon.
    assert stats(sim, "charlie")["cash"] == 145
    assert stats(sim, "charlie")["loan"] == 35
    assert sim.state["globals"]["bank_vault"] == 495
    assert "Day 1: I borrowed $30 from the bank (+$5 fee)." in sim.state["characters"]["charlie"]["memory"]

    run_slots(sim, 2)  # Night, then Day 2 Morning: $10 comes out of the bank account
    assert stats(sim, "charlie")["loan"] == 25
    assert stats(sim, "charlie")["bank"] == 42
    assert sim.state["globals"]["bank_vault"] == 505

    run_slots(sim, 2)  # Afternoon (second borrow is refused: one loan at a time), Night
    assert any("was turned down" in e["text"] for e in sim.state["log"])
    stats(sim, "charlie")["bank"] = 0
    run_slots(sim, 1)  # Day 3 Morning: nothing in the account
    assert stats(sim, "charlie")["missed_payments"] == 1
    assert stats(sim, "charlie")["loan"] == 30
    assert any("missed a loan payment! $5 late fee" in e["text"] for e in sim.state["log"])
    assert any("missed a bank loan payment" in n for n in sim.state["characters"]["charlie"]["memory"])

    run_slots(sim, 2)
    stats(sim, "charlie")["bank"] = 0
    stats(sim, "charlie")["missed_payments"] = 2
    run_slots(sim, 1)  # third strike
    assert stats(sim, "charlie")["missed_payments"] == 3
    assert any("now in default" in e["text"] for e in sim.state["log"])
    # the house was the collateral: the debt is wiped, the bank owns it, and Charlie is evicted
    assert stats(sim, "charlie")["loan"] == 0
    assert stats(sim, "charlie")["house"] == "Bank-owned"
    assert any("The bank took their house" in e["text"] for e in sim.state["log"])
    run_slots(sim, 2)  # Afternoon, Night (+ bedtime)
    assert sim.state["characters"]["charlie"]["location"] == "back_alley"
    asyncio.run(sim._walk_to("charlie", "charlie_home"))
    assert sim.state["characters"]["charlie"]["location"] == "back_alley"
    assert any("Charlie's Room locked" in e["text"] for e in sim.state["log"])


def test_loan_payoff(sim):
    stats(sim, "charlie")["loan"] = 7
    run_slots(sim, 1)  # Morning: loan is under one payment, so it is paid off exactly
    assert stats(sim, "charlie")["loan"] == 0
    assert stats(sim, "charlie")["bank"] == 19
    assert sim.state["globals"]["bank_vault"] == 532


def test_events_run_once_per_slot(sim):
    # A restart mid-slot re-runs the slot; the Morning loan payment must not be taken twice.
    stats(sim, "charlie")["loan"] = 30
    sim._run_events("main")
    sim._run_events("main")
    assert stats(sim, "charlie")["loan"] == 20
    assert stats(sim, "charlie")["bank"] == 16
    run_slots(sim, 1)
    assert stats(sim, "charlie")["loan"] == 20
    assert sim.state["events_done"] == []


def test_default_blocks_borrow(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "borrow", "amount": 30, "move_to": "town_bank"}})
    stats(sim, "charlie")["missed_payments"] = 3
    run_slots(sim, 2)
    assert stats(sim, "charlie")["loan"] == 0
    assert stats(sim, "charlie")["cash"] == 115
    assert sim.state["globals"]["bank_vault"] == 525
    assert any("was turned down" in e["text"] for e in sim.state["log"])


def test_repay_money(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "repay_money", "target": "bob", "amount": 10}})
    run_slots(sim, 2)  # Bob lends $10 in the Morning, Charlie pays it back in the Afternoon
    assert stats(sim, "charlie")["owes"] == {}
    assert stats(sim, "charlie")["cash"] == 105
    assert "Day 1: I paid Bob back $10." in sim.state["characters"]["charlie"]["memory"]
    assert "Day 1: Charlie paid me back $10." in sim.state["characters"]["bob"]["memory"]


def test_lend_needs_owes_stat(world, tmp_path):
    w, rules, characters = world
    chars = dict(characters)
    chars["bob"] = characters["bob"].model_copy(
        update={"mock_plan": {"Morning": {"action": "lend_money", "target": "greg", "amount": 10}}}
    )
    sim = make_sim(w, rules, chars, tmp_path)
    run_slots(sim, 1)
    assert stats(sim, "bob")["cash"] == 88
    assert any("wanted to lend money but could not" in e["text"] for e in sim.state["log"])


def test_payday_needs_treasury(sim_no_plans):
    # An empty treasury pays nobody and never goes below zero; only Mabel's $10 Morning grocery run comes in.
    sim_no_plans.state["globals"]["town_treasury"] = 0
    run_slots(sim_no_plans, 2)
    assert sim_no_plans.state["globals"]["town_treasury"] == 10
    assert stats(sim_no_plans, "bob")["bank"] == 44
    assert any("could not pay" in e["text"] for e in sim_no_plans.state["log"])


def test_treasury_floor(sim_no_plans):
    # $130 + Mabel's $10 Morning grocery run = $140 by payday. Bob (daily_pay 44) needs $144 to keep the $100
    # floor; Charlie ($26) needs $126, so only Charlie is paid.
    sim_no_plans.state["globals"]["town_treasury"] = 130
    run_slots(sim_no_plans, 2)
    assert stats(sim_no_plans, "bob")["bank"] == 44
    assert stats(sim_no_plans, "charlie")["bank"] == 52
    assert sim_no_plans.state["globals"]["town_treasury"] == 114
    assert any("at its minimum and could not pay Bob" in e["text"] for e in sim_no_plans.state["log"])


def test_vault_floor(world, tmp_path):
    sim = bank_sim(world, tmp_path, {"charlie": {"action": "borrow", "amount": 30, "move_to": "town_bank"}})
    sim.state["globals"]["bank_vault"] = 170  # $30 would leave $140, under the $150 floor
    run_slots(sim, 2)
    assert stats(sim, "charlie")["loan"] == 0
    assert sim.state["globals"]["bank_vault"] == 170
    assert any("was turned down" in e["text"] for e in sim.state["log"])


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
    for i in range(23):  # greg + 23 clones fill security_desk (capacity 6x4 = 24)
        cid = f"guard{i}"
        chars[cid] = mabel.model_copy(update={"id": cid, "name": cid, "home": "security_desk", "schedule": desk, "off_duty": {}})
    chars["charlie"] = characters["charlie"].model_copy(
        update={"mock_plan": {"Morning": {"action": "none", "move_to": "security_desk"}}}
    )
    sim = make_sim(w, rules, chars, tmp_path)
    run_slots(sim, 1)
    assert sim.state["characters"]["charlie"]["location"] == "engineering_benches"
    assert sum(1 for cs in sim.state["characters"].values() if cs["location"] == "security_desk") == 24


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
    assert reloaded.state["characters"]["nell"] == {"location": "local_tavern", "stats": {"cash": 3}, "memory": []}
    for cid, s in before.items():
        assert reloaded.state["characters"][cid]["stats"] == s


def test_bedtime(sim_no_plans):
    waits = []

    async def record(seconds):
        waits.append(seconds)

    sim_no_plans._sleep = record
    run_slots(sim_no_plans, 3)  # after Night everyone walks to their own home before Day 2
    for cid, char in sim_no_plans.characters.items():
        assert sim_no_plans.state["characters"][cid]["location"] == char.home
    assert sim_no_plans.state["clock"] == {"day": 2, "slot_index": 0}
    # the bedtime wait is the walk home plus the time asleep (15 s), all before Morning
    chars = sim_no_plans.characters.values()
    walk_home = sim_no_plans._walk_seconds([(c.schedule.get("Night") or c.home, c.home) for c in chars])
    assert walk_home + 15 in waits


def test_all_llm(sim):
    sim.settings.all_llm = True  # local (Ollama) mode: scripted and hybrid characters use the LLM too
    run_slots(sim, 1)
    guards = {cid for cid, c in sim.characters.items() if c.guard}  # guards only speak on patrol
    assert {cid for cid, _ in sim.client.calls} == set(sim.characters) - guards


def test_all_llm_kinds(world, tmp_path):
    w, rules, characters = world
    sim = make_sim(w, rules, characters, tmp_path)
    sim.settings.all_llm = True
    sim = type(sim)(w, rules, characters, sim.client, tmp_path, sim.settings, sim.hub)
    assert {c.kind for c in sim.characters.values()} == {"llm"}


def knock(sim, owner_reply):
    async def reply(*, char, slot, prompt, model):
        return {"say": "Who is it?", "action": owner_reply, "target": None, "amount": None, "move_to": None}

    sim.client.generate_turn = reply
    sim.state["characters"]["bob"]["location"] = "bob_home"
    sim.state["characters"]["charlie"]["location"] = "local_tavern"
    asyncio.run(sim._walk_to("charlie", "bob_home"))
    return sim.state["characters"]["charlie"]["location"]


def test_knock_owner_away(sim):
    sim.state["characters"]["bob"]["location"] = "engineering_benches"
    asyncio.run(sim._walk_to("charlie", "bob_home"))
    assert sim.state["characters"]["charlie"]["location"] != "bob_home"
    assert "No one is home" in sim.state["log"][-1]["text"]


def test_knock_let_in(sim):
    assert knock(sim, "let_in") == "bob_home"


def test_knock_refused(sim):
    assert knock(sim, "none") == "local_tavern"


def test_talk_walks_to_target(sim):
    sim.settings.all_llm = True

    async def turn(*, char, slot, prompt, model):
        if char.id == "bob":
            return {"say": "Hi Charlie.", "action": "none", "target": "charlie", "amount": None, "move_to": None}
        return {"say": "", "action": "none", "target": None, "amount": None, "move_to": None}

    sim.client.generate_turn = turn
    sim.characters["charlie"].schedule["Morning"] = "local_tavern"
    speeches = []
    publish = sim.hub.publish

    def watch(event):
        if event["type"] == "speech":
            speeches.append(event)
        return publish(event)

    sim.hub.publish = watch
    run_slots(sim, 1)
    assert sim.state["characters"]["bob"]["location"] == "local_tavern"
    assert {"speaker": "bob", "to": "charlie"}.items() <= next(e for e in speeches if e["speaker"] == "bob").items()


def night_sim(world, tmp_path, plans):
    """No seed plans, except the given characters' Night plans."""
    w, rules, characters = world
    chars = {cid: c.model_copy(update={"mock_plan": {}}) for cid, c in characters.items()}
    for cid, plan in plans.items():
        chars[cid] = chars[cid].model_copy(update={"mock_plan": {"Night": plan}})
    return make_sim(w, rules, chars, tmp_path)


def watch_events(sim):
    seen = []
    publish = sim.hub.publish

    def watch(event):
        seen.append(event)
        return publish(event)

    sim.hub.publish = watch
    return seen


def test_guard_confronts_drunk(sim):
    seen = watch_events(sim)
    run_slots(sim, 3)  # seed plans: Charlie drinks at Night
    assert {"type": "move", "character": "greg", "to": "local_tavern"} in seen
    speech = next(e for e in seen if e["type"] == "speech" and e["speaker"] == "greg")
    assert speech["to"] == "charlie"
    assert speech["text"] == "Charlie, I'm sorry, but you've had too much. Please keep it down."


def test_guard_quiet_without_violations(sim_no_plans):
    run_slots(sim_no_plans, 3)
    assert not any(e["speaker"] == "greg" for e in sim_no_plans.state["log"])
    assert not any(cid == "greg" for cid, _ in sim_no_plans.client.calls)


def test_guard_missed_rent(sim_no_plans):
    stats(sim_no_plans, "charlie")["cash"] = 0
    run_slots(sim_no_plans, 4)  # rent is missed at Night while Charlie sleeps; Greg tells him off next Morning
    line = next(e for e in sim_no_plans.state["log"] if e["speaker"] == "greg")
    assert line["text"] == "Excuse me, Charlie, your rent is overdue. Please settle up with Dina before it grows."
    assert line["slot"] == "Morning"


def test_bank_closed_at_night(world, tmp_path):
    sim = night_sim(world, tmp_path, {"charlie": {"action": "withdraw", "amount": 15, "move_to": "town_bank"}})
    seen = watch_events(sim)
    run_slots(sim, 3)
    assert stats(sim, "charlie")["bank"] == 52  # Afternoon pay only, no withdrawal
    assert not any("withdrew" in e["text"] for e in sim.state["log"])
    assert {"type": "move", "character": "charlie", "to": "town_bank"} not in seen  # strictly closed
    assert any(e["text"] == "Charlie finds Town Bank closed." for e in sim.state["log"])
    assert not any(e["speaker"] == "greg" for e in sim.state["log"])  # Charlie stayed home asleep


def test_sleeper_is_silent(world, tmp_path):
    sim = night_sim(world, tmp_path, {"bob": {"say": "Zzz... talking in bed.", "action": "none"}})
    run_slots(sim, 3)
    assert not any(e["speaker"] == "bob" and e["slot"] == "Night" for e in sim.state["log"])


def test_sleeping_owner_no_answer(sim):
    sim.state["clock"]["slot_index"] = 2  # Night
    calls = []

    async def reply(*, char, slot, prompt, model):
        calls.append(char.id)
        return {"say": "Come in.", "action": "let_in", "target": None, "amount": None, "move_to": None}

    sim.client.generate_turn = reply
    sim.state["characters"]["bob"]["location"] = "bob_home"
    sim.state["characters"]["charlie"]["location"] = "local_tavern"
    asyncio.run(sim._walk_to("charlie", "bob_home"))
    assert sim.state["characters"]["charlie"]["location"] == "local_tavern"
    assert sim.state["log"][-1]["text"] == "Charlie knocks on the door of Bob's House. No one answers."
    assert calls == []


def test_overtime(world, tmp_path):
    sim = night_sim(world, tmp_path, {"bob": {"action": "overtime", "move_to": "engineering_benches"}})
    run_slots(sim, 3)
    assert stats(sim, "bob")["bank"] == 106  # $44 pay + $18 overtime
    assert stats(sim, "bob")["overtime"] is False  # reset after Night
    assert not any(e["speaker"] == "greg" for e in sim.state["log"])


def test_no_overtime_is_after_hours(world, tmp_path):
    sim = night_sim(world, tmp_path, {"bob": {"action": "none", "move_to": "engineering_benches"}})
    run_slots(sim, 3)
    greg = [e["text"] for e in sim.state["log"] if e["speaker"] == "greg"]
    assert greg == ["Sorry, Bob, this place is closed at night. Please head home."]


def test_local_stats(world, tmp_path):
    w, rules, characters = world
    greg = {"cash": 70, "bank": 35, **LOAN_STATS, **CARD_STATS, **JAIL_STATS, **BURGLARY_STATS}
    assert make_sim(w, rules, characters, tmp_path / "a").state["characters"]["greg"]["stats"] == greg
    sim = make_sim(w, rules, characters, tmp_path / "b")
    sim.settings.all_llm = True
    local = type(sim)(w, rules, characters, sim.client, tmp_path / "b", sim.settings, sim.hub)
    assert local.state["characters"]["greg"]["stats"] == {"cash_pay": 35, "rent_due": 20, **greg}
    assert local.state["characters"]["bob"]["stats"]["rent_due"] == 20
    assert local.state["characters"]["dina"]["stats"] == {
        "cash": 70, "bank": 175, "daily_pay": 26, **LOAN_STATS, **CARD_STATS, **JAIL_STATS, **BURGLARY_STATS
    }


def morning_sim(world, tmp_path, plans):
    """No seed plans, except the given characters' Morning plans."""
    w, rules, characters = world
    chars = {cid: c.model_copy(update={"mock_plan": {}}) for cid, c in characters.items()}
    for cid, plan in plans.items():
        chars[cid] = chars[cid].model_copy(update={"mock_plan": {"Morning": plan}})
    return make_sim(w, rules, chars, tmp_path)


def test_alone_is_silent(world, tmp_path):
    sim = morning_sim(world, tmp_path, {"bob": {"say": "Morning, Charlie.", "action": "none"}})
    run_slots(sim, 1)  # Bob and Charlie are both in the Dev Studio
    line = next(e for e in sim.state["log"] if e["speaker"] == "bob")
    assert line["where"] == "Dev Studio"

    sim = morning_sim(world, tmp_path / "b", {"bob": {"say": "Anyone here?", "action": "none"}})
    sim.characters["bob"].schedule["Morning"] = "bob_home"  # alone at home in the Morning
    run_slots(sim, 1)
    assert not any(e["speaker"] == "bob" for e in sim.state["log"])


def test_history_shows_where(world, tmp_path):
    from engine.prompts import build_prompt

    sim = morning_sim(world, tmp_path, {"bob": {"say": "Morning, Charlie.", "action": "none"}})
    run_slots(sim, 1)
    prompt = build_prompt(sim.characters["charlie"], sim.state, sim.world, sim.rules, sim.characters)
    assert "[Dev Studio] Bob: Morning, Charlie." in prompt


def test_no_talking_to_a_sleeper(world, tmp_path):
    sim = night_sim(world, tmp_path, {"dina": {"say": "Bob! You up?", "action": "none", "target": "bob"}})
    sim.settings.all_llm = True  # Dina takes an LLM turn at the tavern
    seen = watch_events(sim)
    run_slots(sim, 3)  # Bob is asleep at home at Night
    assert not any(e["speaker"] == "dina" and e["slot"] == "Night" for e in sim.state["log"])
    assert {"type": "move", "character": "dina", "to": "bob_home"} not in seen

    sim = night_sim(world, tmp_path / "b", {"dina": {"say": "Evening, Mabel.", "action": "none", "target": "mabel"}})
    sim.settings.all_llm = True
    run_slots(sim, 3)  # Mabel is awake at the tavern
    assert any(e["speaker"] == "dina" and e["text"] == "Evening, Mabel." for e in sim.state["log"])


def talk_client(sim, actions):
    """Replies say who speaks; each call takes the next action from `actions` (the last one repeats)."""
    calls = []

    async def turn(*, char, slot, prompt, model):
        calls.append(char.id)
        action = actions[min(len(calls), len(actions)) - 1]
        return {"say": f"{char.name} {len(calls)}", "action": action, "target": None, "amount": None, "move_to": None}

    sim.client.generate_turn = turn
    return calls


def test_converse_until_end(sim):
    from engine.simulation import TALK_MAX

    for cid in ("bob", "charlie"):
        sim.state["characters"][cid]["location"] = "engineering_benches"
    talk_client(sim, ["continue", "continue", "end", "continue"])
    seen = watch_events(sim)
    asyncio.run(sim._converse("bob", "charlie", "Hi"))
    lines = [(e["speaker"], e["to"]) for e in seen if e["type"] == "speech"]
    assert lines == [("charlie", "bob"), ("bob", "charlie"), ("charlie", "bob")]  # Charlie ends it
    assert seen[0] == {"type": "talk", "pair": ["bob", "charlie"], "open": True}
    assert seen[-1] == {"type": "talk", "pair": ["bob", "charlie"], "open": False}

    seen.clear()
    talk_client(sim, ["continue"])  # nobody says goodbye
    asyncio.run(sim._converse("bob", "charlie", "Hi"))
    assert len([e for e in seen if e["type"] == "speech"]) == TALK_MAX


def test_converse_closes_talk_on_early_exit(sim):
    sim.state["characters"]["bob"]["location"] = "engineering_benches"
    sim.state["characters"]["charlie"]["location"] = "local_tavern"  # different rooms: no reply
    talk_client(sim, ["continue"])
    seen = watch_events(sim)
    asyncio.run(sim._converse("bob", "charlie", "Hi"))
    assert seen == [
        {"type": "talk", "pair": ["bob", "charlie"], "open": True},
        {"type": "talk", "pair": ["bob", "charlie"], "open": False},
    ]


def test_no_reply_from_sleeper(world, tmp_path):
    sim = night_sim(world, tmp_path, {})
    run_slots(sim, 2)  # now Night
    sim.state["characters"]["bob"]["location"] = "bob_home"  # Bob is asleep at home
    sim.state["characters"]["charlie"]["location"] = "bob_home"
    calls = talk_client(sim, ["continue"])
    asyncio.run(sim._converse("charlie", "bob", "Bob?"))
    assert calls == []


def test_landlord_rounds(world, tmp_path):
    w, rules, characters = world
    sim = make_sim(w, rules, characters, tmp_path)
    sim.settings.all_llm = True  # local mode: everyone but Dina pays rent
    sim = type(sim)(w, rules, characters, sim.client, tmp_path, sim.settings, sim.hub)
    met = []

    async def turn(*, char, slot, prompt, model):
        if "your tenant" in prompt:
            tenant = next(c for c in sim.order if f"up to {sim.names[c]}," in prompt)
            chars = sim.state["characters"]
            met.append((tenant, chars["dina"]["location"] == chars[tenant]["location"]))
        return {"say": f"{char.name} talking.", "action": "end", "target": None, "amount": None, "move_to": None}

    sim.client.generate_turn = turn
    seen = watch_events(sim)
    run_slots(sim, 2)  # Morning, then Afternoon rounds
    tenants = [c for c in sim.order if c != "dina" and "rent_due" in stats(sim, c)]
    assert [t for t, _ in met] == tenants  # one by one
    assert all(same for _, same in met)  # face to face
    speech = [e for e in seen if e["type"] == "speech"]
    to_tenants = [(i, e) for i, e in enumerate(speech) if e["speaker"] == "dina" and e.get("to")]
    assert [e["to"] for _, e in to_tenants] == tenants
    for i, e in to_tenants:  # each tenant answers her
        assert (speech[i + 1]["speaker"], speech[i + 1]["to"]) == (e["to"], "dina")


# ---- punch, jail, bail, health ---------------------------------------------


def plan_sim(world, tmp_path, plans):
    """No seed plans, except the given {cid: {slot: plan}}."""
    w, rules, characters = world
    chars = {cid: c.model_copy(update={"mock_plan": {}}) for cid, c in characters.items()}
    for cid, plan in plans.items():
        chars[cid] = chars[cid].model_copy(update={"mock_plan": plan})
    return make_sim(w, rules, chars, tmp_path)


def lock_up(sim, cid):
    stats(sim, cid)["jailed"] = True
    sim.state["characters"][cid]["location"] = "jail"


def texts(sim):
    return [e["text"] for e in sim.state["log"]]


def test_punch_lowers_health(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"bob": {"Morning": {"action": "punch", "target": "charlie"}}})
    run_slots(sim, 1)
    assert stats(sim, "charlie")["health"] == 80
    assert stats(sim, "charlie")["hit_by"] == {"bob": 1}
    assert "Fight: Bob punched Charlie!" in texts(sim)
    assert "Day 1: Bob punched me. I can complain to Greg about it (I will walk to him)." in \
        sim.state["characters"]["charlie"]["memory"]


def test_punch_walks_up_and_floors_at_zero(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"bob": {"Morning": {"action": "punch", "target": "tess"}}})
    stats(sim, "tess")["health"] = 10
    run_slots(sim, 1)
    assert sim.state["characters"]["bob"]["location"] == "town_bank"  # walked over to Tess first
    # 10 - 20 floors at 0; then Greg sends the first fit helper (Charlie), who adds 30
    assert stats(sim, "tess")["health"] == 30
    assert "Aid: Charlie helped Tess back on their feet." in texts(sim)


def test_complain_then_arrest(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Morning": {"action": "complain", "target": "bob"}}})
    seen = watch_events(sim)
    stats(sim, "charlie")["hit_by"] = {"bob": 1}
    run_slots(sim, 1)
    assert sim.state["characters"]["charlie"]["location"] == "security_desk"  # walked to Greg to report it
    assert stats(sim, "charlie")["hit_by"] == {}
    assert any(e["speaker"] == "greg" and e["text"] == "I'll bring Bob in, Charlie." for e in sim.state["log"])
    assert stats(sim, "bob")["jailed"] is True
    assert stats(sim, "bob")["wanted"] is False
    assert sim.state["characters"]["bob"]["location"] == "jail"
    assert sim.state["characters"]["greg"]["location"] == "jail"  # walked him to the cell
    assert {"type": "move", "character": "greg", "to": "jail"} in seen
    assert "Jail: Greg locked Bob in the jail cell." in texts(sim)
    arrest = next(e for e in seen if e["type"] == "speech" and e["speaker"] == "greg" and e.get("to") == "bob")
    assert arrest["text"] == "Bob, you're under arrest for assault. You're coming with me to the cell."

    stats(sim, "bob")["health"] = 50
    run_slots(sim, 2)  # Afternoon, Night: stays in the cell, sleeps there, no health change
    assert sim.state["characters"]["bob"]["location"] == "jail"
    assert stats(sim, "bob")["health"] == 50
    asyncio.run(sim._walk_to("bob", "local_tavern"))
    assert sim.state["characters"]["bob"]["location"] == "jail"
    assert "Bob is locked in the jail cell." in texts(sim)


def test_complain_needs_a_punch(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Morning": {"action": "complain", "target": "bob"}}})
    run_slots(sim, 1)
    assert stats(sim, "bob")["wanted"] is False
    assert stats(sim, "bob")["jailed"] is False
    assert "Report: Charlie tried to report Bob to Greg but could not." in texts(sim)


def test_apologize_then_forgive(world, tmp_path):
    sim = plan_sim(world, tmp_path, {
        "bob": {"Morning": {"action": "apologize", "target": "charlie"}},
        "charlie": {"Morning": {"action": "forgive", "target": "bob"}, "Afternoon": {"action": "forgive", "target": "bob"}},
    })
    lock_up(sim, "bob")
    run_slots(sim, 1)  # Charlie's turn comes before Bob's: nothing to forgive yet
    assert "Charlie wanted to forgive Bob, but they never asked." in texts(sim)
    assert stats(sim, "bob")["sorry"] == {"charlie": 1}
    assert sim.state["characters"]["bob"]["location"] == "jail"
    run_slots(sim, 1)
    assert stats(sim, "bob")["jailed"] is False
    assert stats(sim, "bob")["sorry"] == {}
    assert "Charlie forgave Bob." in texts(sim)


def test_bail_by_another(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Morning": {"action": "bail", "target": "bob"}}})
    lock_up(sim, "bob")
    run_slots(sim, 1)
    assert sim.state["characters"]["charlie"]["location"] == "jail"  # walked to the jail to pay
    assert stats(sim, "charlie")["cash"] == 75
    assert sim.state["globals"]["town_treasury"] == 390  # + $30 bail + Mabel's $10 Morning grocery run
    assert stats(sim, "bob")["jailed"] is False
    assert stats(sim, "bob")["bail"] == 50  # repeat stays cost more
    assert "Jail: Charlie paid Bob's bail." in texts(sim)


def test_self_bail_and_ask_bail(world, tmp_path):
    sim = plan_sim(world, tmp_path, {
        "bob": {"Morning": {"action": "ask_bail", "target": "charlie"}, "Afternoon": {"action": "post_bail"}},
    })
    lock_up(sim, "bob")
    run_slots(sim, 1)
    assert any("Bob is in the jail cell and asked me to pay their bail" in n
               for n in sim.state["characters"]["charlie"]["memory"])
    run_slots(sim, 1)
    assert stats(sim, "bob")["jailed"] is False
    assert stats(sim, "bob")["cash"] == 58
    assert stats(sim, "bob")["bail"] == 50


def test_health_after_night(sim_no_plans):
    sim = sim_no_plans
    stats(sim, "bob")["health"] = 40
    stats(sim, "charlie")["house"] = "Bank-owned"
    stats(sim, "charlie")["health"] = 50
    lock_up(sim, "mabel")
    stats(sim, "mabel")["health"] = 50
    run_slots(sim, 3)
    assert stats(sim, "bob")["health"] == 100  # a night at home
    assert stats(sim, "charlie")["health"] == 40  # evicted, nobody took them in
    assert sim.state["characters"]["charlie"]["location"] == "back_alley"
    assert stats(sim, "mabel")["health"] == 50  # jailed: no change
    assert sim.state["characters"]["mabel"]["location"] == "jail"

    stats(sim, "charlie")["hosts"] = {"bob": 1}
    run_slots(sim, 3)
    assert stats(sim, "charlie")["health"] == 100
    assert sim.state["characters"]["charlie"]["location"] == "bob_home"


def test_offer_stay(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"bob": {"Morning": {"action": "offer_stay", "target": "charlie"}}})
    stats(sim, "charlie")["house"] = "Bank-owned"
    run_slots(sim, 1)
    assert stats(sim, "charlie")["hosts"] == {"bob": 1}
    assert "Home: Bob offered Charlie a place to stay." in texts(sim)


def test_guard_sends_help(sim_no_plans):
    seen = watch_events(sim_no_plans)
    stats(sim_no_plans, "charlie")["health"] = 15
    run_slots(sim_no_plans, 1)
    ask = next(e for e in seen if e["type"] == "speech" and e["speaker"] == "greg")
    assert ask["to"] == "bob"
    assert ask["text"] == "Bob, Charlie is badly hurt. Please go and help them."
    assert stats(sim_no_plans, "charlie")["health"] == 45
    assert "Aid: Bob helped Charlie back on their feet." in texts(sim_no_plans)


def test_no_drinking_out_of_jail(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Night": {"action": "drink"}}})
    lock_up(sim, "charlie")
    run_slots(sim, 3)
    assert stats(sim, "charlie")["sobriety"] == "Sober"
    assert sim.state["characters"]["charlie"]["location"] == "jail"


def test_rent_split(sim_no_plans):
    run_slots(sim_no_plans, 3)  # Charlie pays $20 rent at Night: $5 to Dina's bank (plus her $26 dev pay), $15 to the treasury
    assert stats(sim_no_plans, "dina")["bank"] == 206
    assert stats(sim_no_plans, "charlie")["cash"] == 85


def test_buy_food_cash_and_card(world, tmp_path):
    sim = plan_sim(world, tmp_path, {
        "bob": {"Morning": {"action": "buy_food_cash", "move_to": "grocery"}},
        "charlie": {"Morning": {"action": "buy_food_card", "move_to": "grocery"}},
    })
    stats(sim, "bob")["health"] = 50
    stats(sim, "charlie")["health"] = 95
    run_slots(sim, 1)
    assert stats(sim, "bob")["cash"] == 78 and stats(sim, "bob")["health"] == 65
    assert stats(sim, "charlie")["bank"] == 16 and stats(sim, "charlie")["health"] == 100  # capped at 100
    assert "Shopping: Bob bought food with cash." in texts(sim)
    assert "Shopping: Charlie bought food with their debit card." in texts(sim)


def test_buy_food_credit(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Afternoon": {"action": "buy_food_credit", "move_to": "grocery"}}})
    stats(sim, "charlie")["credit_card"] = True
    run_slots(sim, 2)  # bought in the Afternoon; the first card payment is next Morning
    assert stats(sim, "charlie")["card_balance"] == 10
    assert stats(sim, "charlie")["bank"] == 52  # nothing from the account yet
    assert sim.state["globals"]["bank_vault"] == 515  # the bank paid the store
    assert "Shopping: Charlie bought food on their credit card." in texts(sim)


def test_credit_needs_card_and_limit(world, tmp_path):
    plans = {"charlie": {"Morning": {"action": "buy_food_credit", "move_to": "grocery"}}}
    no_card = plan_sim(world, tmp_path / "a", plans)
    run_slots(no_card, 1)
    at_limit = plan_sim(world, tmp_path / "b", plans)
    stats(at_limit, "charlie")["credit_card"] = True
    stats(at_limit, "charlie")["card_balance"] = 195
    run_slots(at_limit, 1)
    for sim in (no_card, at_limit):
        assert "Shopping: Charlie tried to pay for food on credit but the card was declined." in texts(sim)
    assert stats(at_limit, "charlie")["card_balance"] == 185  # no purchase; only the Morning $10 payment


def test_get_credit_card_needs_200(world, tmp_path):
    plan = {"charlie": {"action": "get_credit_card", "move_to": "town_bank"}}
    poor = bank_sim(world, tmp_path / "a", plan)
    run_slots(poor, 2)
    assert stats(poor, "charlie")["credit_card"] is False
    assert "Financial Action: Charlie asked the bank for a credit card but was turned down." in texts(poor)
    rich = bank_sim(world, tmp_path / "b", plan)
    stats(rich, "charlie")["bank"] = 200
    run_slots(rich, 2)
    assert stats(rich, "charlie")["credit_card"] is True
    assert any(e["speaker"] == "tess" and e["text"].startswith("Approved, Charlie. Your card") for e in rich.state["log"])


def test_card_payment_and_default(sim_no_plans):
    sim = sim_no_plans
    stats(sim, "charlie")["credit_card"] = True
    stats(sim, "charlie")["card_balance"] = 30
    run_slots(sim, 1)  # Morning: $10 from the bank account
    assert stats(sim, "charlie")["card_balance"] == 20
    assert stats(sim, "charlie")["bank"] == 16
    assert sim.state["globals"]["bank_vault"] == 535
    run_slots(sim, 2)
    stats(sim, "charlie")["bank"] = 0
    run_slots(sim, 1)  # Day 2 Morning: nothing in the account
    assert stats(sim, "charlie")["card_missed"] == 1
    assert stats(sim, "charlie")["card_balance"] == 25
    assert "CRISIS: Charlie missed a credit card payment! $5 late fee added." in texts(sim)
    run_slots(sim, 2)
    stats(sim, "charlie")["bank"] = 0
    stats(sim, "charlie")["card_missed"] = 2
    run_slots(sim, 1)  # third strike: the house is the collateral
    assert stats(sim, "charlie")["card_missed"] == 3
    assert stats(sim, "charlie")["card_balance"] == 0
    assert stats(sim, "charlie")["credit_card"] is False
    assert stats(sim, "charlie")["house"] == "Bank-owned"


def test_two_checkout_lanes(sim_no_plans):
    sim = sim_no_plans
    for cid in ("bob", "charlie", "dina"):
        sim.state["characters"][cid]["location"] = "grocery"
    spots = []
    publish = sim.hub.publish

    def watch(event):
        if event["type"] == "state":
            cs = event["state"]["characters"]
            spots.append((cs["bob"].get("spot"), cs["charlie"].get("spot"), cs["dina"].get("spot")))
        return publish(event)

    sim.hub.publish = watch

    async def go():
        for cid in ("bob", "charlie", "dina"):
            await sim._take_spot(cid, "checkout")
    asyncio.run(go())
    assert ("checkout_1", "checkout_2", None) in spots  # two at once
    assert ("checkout_1", "checkout_2", "wait") in spots  # the third waits
    assert spots[-1] == (None, "checkout_2", "checkout_1")  # then lane 1 frees up for them


def test_off_duty_shopping(sim_no_plans):
    sim = sim_no_plans
    run_slots(sim, 1)  # Morning: Mabel is off duty and shops; Tess is on duty at the bank
    assert sim.state["characters"]["mabel"]["location"] == "grocery"
    assert "Shopping: Mabel bought food with their debit card." in texts(sim)
    assert stats(sim, "mabel")["bank"] == 25
    asyncio.run(sim._walk_to("tess", "grocery"))
    assert sim.state["characters"]["tess"]["location"] == "town_bank"
    run_slots(sim, 2)  # Afternoon, Night: Tess shops after work
    assert "Shopping: Tess bought food with their debit card." in texts(sim)
    assert stats(sim, "tess")["bank"] == 25


def test_card_refused_on_penalty(world, tmp_path):
    plan = {"charlie": {"action": "get_credit_card", "move_to": "town_bank"}}
    for i, (stat, value) in enumerate([("rent_due", 40), ("missed_payments", 1), ("card_missed", 1), ("wanted", True)]):
        sim = bank_sim(world, tmp_path / str(i), plan)
        stats(sim, "charlie")["bank"] = 200
        stats(sim, "charlie")[stat] = value
        run_slots(sim, 2)
        assert stats(sim, "charlie")["credit_card"] is False, stat
        assert "Financial Action: Charlie asked the bank for a credit card but was turned down." in texts(sim)


def test_dina_loan_blocks_card(world, tmp_path):
    plan = {"dina": {"Morning": {"action": "get_credit_card", "move_to": "town_bank"}}}
    clean = plan_sim(world, tmp_path / "a", plan)
    clean.settings.all_llm = True  # Dina takes an LLM turn in the Morning
    stats(clean, "dina")["bank"] = 200  # no rent_due stat outside local mode: counts as no penalty
    run_slots(clean, 1)
    assert stats(clean, "dina")["credit_card"] is True
    missed = plan_sim(world, tmp_path / "b", plan)
    missed.settings.all_llm = True
    stats(missed, "dina").update(bank=200, loan=15, missed_payments=1)
    run_slots(missed, 1)
    assert stats(missed, "dina")["credit_card"] is False


def test_guard_turns(world, tmp_path):
    plan = {"greg": {"Morning": {"action": "borrow", "amount": 20, "move_to": "town_bank"}}}
    off = plan_sim(world, tmp_path / "a", plan)
    run_slots(off, 1)
    assert stats(off, "greg")["loan"] == 0  # mock mode: guards only patrol
    on = plan_sim(world, tmp_path / "b", plan)
    on.settings.guard_turns = True
    run_slots(on, 1)
    assert "Financial Action: Greg borrowed $20 from the bank." in texts(on)
    assert stats(on, "greg")["cash"] == 90
    assert stats(on, "greg")["loan"] == 15  # $20 + $5 fee, less the Morning $10 payment


def test_office_needs_dina(sim):
    sim.state["characters"]["dina"]["location"] = "engineering_benches"
    sim.state["characters"]["charlie"]["location"] = "local_tavern"
    asyncio.run(sim._walk_to("charlie", "landlord_office"))
    assert sim.state["characters"]["charlie"]["location"] == "local_tavern"
    assert "No one is home" in sim.state["log"][-1]["text"]


def test_break_in_steals(world, tmp_path):
    sim = plan_sim(world, tmp_path, {"charlie": {"Morning": {"action": "break_in", "target": "bob"}}})
    bob_cash, charlie_cash = stats(sim, "bob")["cash"], stats(sim, "charlie")["cash"]
    run_slots(sim, 1)  # Bob is at the Dev Studio: nobody sees it
    assert sim.state["characters"]["charlie"]["location"] == "bob_home"
    assert stats(sim, "charlie")["cash"] == charlie_cash + 20
    assert stats(sim, "bob")["cash"] == bob_cash - 20
    assert stats(sim, "charlie")["stolen"] == 20
    assert stats(sim, "charlie")["burglaries"] == 1
    assert stats(sim, "charlie")["intruder"] == "bob_home"
    assert "Crime: Charlie broke into Bob's house." in texts(sim)


def test_owner_catches_reports_and_greg_jails(world, tmp_path):
    sim = plan_sim(world, tmp_path, {
        "charlie": {"Morning": {"action": "break_in", "target": "bob"}},
        "bob": {"Morning": {"move_to": "bob_home"}, "Afternoon": {"action": "report_burglary", "target": "charlie"}},
    })
    run_slots(sim, 1)
    assert stats(sim, "bob")["saw_burglary"] == {"charlie": 1}
    assert stats(sim, "charlie")["intruder"] == ""
    assert "Crime: Bob caught Charlie breaking into Bob's House." in texts(sim)
    run_slots(sim, 1)  # Afternoon: Bob tells Greg; Greg questions Charlie, who stole money -> jail (fixed rule in mock)
    assert stats(sim, "bob")["saw_burglary"] == {}
    assert stats(sim, "charlie")["jailed"] is True
    assert stats(sim, "charlie")["burglar_wanted"] is False
    assert stats(sim, "charlie")["stolen"] == 0
    assert sim.state["characters"]["charlie"]["location"] == "jail"
    assert "Jail: Greg locked Charlie in the jail cell for burglary." in texts(sim)


def test_greg_catches_and_warns_first_empty_handed(sim_no_plans):
    sim = sim_no_plans
    chars = sim.state["characters"]
    chars["charlie"]["location"] = chars["greg"]["location"] = "bob_home"
    chars["bob"]["location"] = "engineering_benches"
    stats(sim, "charlie").update({"intruder": "bob_home", "burglaries": 1, "stolen": 0})
    asyncio.run(sim._patrol())  # Greg is in the room: he catches Charlie himself, then questions him
    assert stats(sim, "charlie")["jailed"] is False
    assert stats(sim, "charlie")["burglar_wanted"] is False
    assert "Crime: Greg caught Charlie breaking into Bob's House." in texts(sim)
    assert "Warning: Greg let Charlie off with a warning for burglary." in texts(sim)


def test_burglary_verdict_by_ai(sim_no_plans):
    sim = sim_no_plans

    class Live:  # not the mock client: Greg's own answer decides
        async def generate_turn(self, *, char, slot, prompt, model):
            action = "jail" if "jail" in prompt and char.id == "greg" and "Decide" in prompt else "end"
            return {"say": "You're going to the cell.", "action": action, "target": None, "amount": None, "move_to": None}

    sim.client = Live()
    sim.budget.allow = lambda: True
    chars = sim.state["characters"]
    chars["charlie"]["location"] = "local_tavern"
    stats(sim, "charlie").update({"burglar_wanted": True, "burglaries": 1, "stolen": 0})  # fixed rule would warn
    asyncio.run(sim._patrol())
    assert stats(sim, "charlie")["jailed"] is True
