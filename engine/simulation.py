import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import npc
from .dsl import Context, DSLError, apply_effects, eval_conditions, format_log
from .llm import CallBudget, MockClient
from .loader import initial_state, reconcile_state
from .prompts import (build_aid_prompt, build_confront_prompt, build_knock_prompt, build_prompt, build_reply_prompt,
                      build_rounds_prompt, build_verdict_prompt)

LOG_KEEP = 200
TALK_MAX = 6  # replies per talk at most; an LLM may never say goodbye
SNAPSHOT_LOG = 50
CAP_NOTICE = "LLM call cap reached; characters are quiet until the window resets."
BANK_OWNED = "Bank-owned"  # stat `house` after a loan default: the owner sleeps at a host's home or world.shelter
CHECKOUT_LANES = 2  # the grocery checkout serves this many at once (spots checkout_1, checkout_2)

logger = logging.getLogger("pixel_town.simulation")


@dataclass
class Settings:
    time_scale: float
    always_run: bool
    max_calls_per_hour: int
    all_llm: bool = False  # every character decides through the LLM (scripted and hybrid too)
    guard_turns: bool = False  # guards also take normal turns (Ollama and Gemini modes), besides patrolling


class Simulation:
    def __init__(self, world, rules, characters, llm_client, runtime_dir, settings, hub):
        self.world = world
        self.rules = rules
        self.characters = characters
        if settings.all_llm:  # local mode: everyone is LLM-driven, and shown and described as such
            for c in characters.values():
                c.kind = "llm"
                c.stats = {**c.local_stats, **c.stats}  # e.g. everyone but the landlord pays rent
        self.client = llm_client
        self.settings = settings
        self.hub = hub
        self.budget = CallBudget(settings.max_calls_per_hour)
        self.dir = Path(runtime_dir) / world.id
        self.state_path = self.dir / "state.json"
        self.chat_path = self.dir / "chat.log"
        self.paused = False
        self._reset_requested = False
        self._cap_noticed = False
        self.names = {cid: c.name for cid, c in characters.items()}
        listed = [cid for cid in world.turn_order if cid in characters]
        self.order = listed + sorted(cid for cid in characters if cid not in listed)
        if self.state_path.exists():
            with open(self.state_path, encoding="utf-8") as f:
                self.state = reconcile_state(json.load(f), world, characters)
        else:
            self.state = initial_state(world, characters)

    # ---- helpers -------------------------------------------------------

    @property
    def slot(self):
        return self.world.clock.slots[self.state["clock"]["slot_index"]]

    async def _sleep(self, seconds):
        if self.settings.time_scale > 0 and seconds > 0:
            await asyncio.sleep(seconds * self.settings.time_scale)

    def _public_state(self):
        clock = self.state["clock"]
        return {
            "clock": {"day": clock["day"], "slot": self.slot, "slot_index": clock["slot_index"]},
            "globals": self.state["globals"],
            "characters": self.state["characters"],
        }

    def _publish_state(self):
        self.hub.publish({"type": "state", "state": self._public_state()})

    def _add_log(self, speaker, name, text):
        log = self.state["log"]
        seq = log[-1]["seq"] + 1 if log else 1
        entry = {"seq": seq, "speaker": speaker, "name": name, "text": text,
                 "day": self.state["clock"]["day"], "slot": self.slot}
        if speaker in self.state["characters"]:
            entry["where"] = self.world.locations[self.state["characters"][speaker]["location"]].name
        log.append(entry)
        del log[:-LOG_KEEP]
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.chat_path, "a", encoding="utf-8") as f:
            f.write(f"[{name}]: {text}\n")

    def _jailed(self, cid):
        return bool(self.state["characters"][cid]["stats"].get("jailed"))

    def _home(self, cid):
        """Where `cid` sleeps: the jail cell while jailed; once the bank owns their home, the home of someone who
        offered a place to stay (stat `hosts`), else the shelter; otherwise their own home."""
        if self._jailed(cid) and self.world.jail:
            return self.world.jail
        stats = self.state["characters"][cid]["stats"]
        if self.world.shelter and stats.get("house") == BANK_OWNED:
            for host in stats.get("hosts") or {}:
                if host in self.characters and self.state["characters"][host]["stats"].get("house") != BANK_OWNED:
                    return self.characters[host].home
            return self.world.shelter
        return self.characters[cid].home

    def _asleep(self, cid):
        """In bed: the last slot (Night), at their own home. Sleepers never speak."""
        return (self.slot == self.world.clock.slots[-1]
                and self.state["characters"][cid]["location"] == self._home(cid))

    def _alone(self, cid):
        """Nobody else in their room, so a turn's words would go to no one."""
        here = self.state["characters"][cid]["location"]
        return not any(o != cid and cs["location"] == here for o, cs in self.state["characters"].items())

    def _speech(self, cid, text, to=None):
        if self._asleep(cid):
            return
        name = self.names[cid]
        self._add_log(cid, name, text)
        event = {"type": "speech", "speaker": cid, "name": name, "text": text}
        if to:
            event["to"] = to  # who they speak to: the browser walks them side by side
        self.hub.publish(event)

    def _system(self, text):
        self._add_log("system", "System", text)
        self.hub.publish({"type": "system", "text": text})

    def _ctx(self, actor=None, target=None, params=None):
        return Context(state=self.state, slot=self.slot, names=self.names,
                       actor=actor, target=target, params=params or {})

    def _apply(self, effects, ctx, label):
        """Apply effects atomically; commit and emit their log lines only on success."""
        try:
            new_state, logs = apply_effects(effects, ctx)
        except DSLError as exc:
            logger.error("[rules] %s: %s", label, exc)
            return
        self.state = new_state
        for line in logs:
            if isinstance(line, tuple):
                self._speech(*line)
            else:
                self._system(line)
        self._publish_state()

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_path)

    # ---- decisions -----------------------------------------------------

    async def _decide(self, char):
        """Returns (decision, used_llm)."""
        if not self.settings.all_llm:
            if char.kind == "scripted":
                return self._scripted(char), False
            if char.kind == "hybrid":
                here = self.state["characters"][char.id]["location"]
                company = any(
                    cid != char.id and self.characters[cid].kind in ("llm", "hybrid") and cs["location"] == here
                    for cid, cs in self.state["characters"].items()
                )
                if not company:
                    return self._scripted(char), False
        if not self.budget.allow():
            if not self._cap_noticed:
                self._cap_noticed = True
                self.hub.publish({"type": "notice", "level": "warn", "text": CAP_NOTICE})
            return self._scripted(char), False
        self._cap_noticed = False
        prompt = build_prompt(char, self.state, self.world, self.rules, self.characters)
        decision = await self.client.generate_turn(
            char=char, slot=self.slot, prompt=prompt, model=char.model or self.world.default_model
        )
        self.budget.record()
        return decision, True

    def _scripted(self, char):
        return npc.decide_scripted(char, self.world.seed, self.state["clock"]["day"], self.slot)

    # ---- actions & events ----------------------------------------------

    def _resolve_action(self, cid, decision):
        char = self.characters[cid]
        action_id = decision.get("action") or "none"
        if action_id not in char.allowed_actions or action_id not in self.rules.actions:
            action_id = "none"
        action = self.rules.actions.get(action_id)
        if action is None:
            return
        if action.only_slots and self.slot not in action.only_slots:
            return

        params, target = {}, None
        if "target" in action.params:
            target = decision.get("target")
            if target not in self.characters or target == cid:
                return
            params["target"] = target
        place = None
        if action.break_into:
            place = self._break_place(cid, action, target)
            if action.break_into != "target":
                target = self.world.locations[place].owner
            if target is None or target == cid or self.state["characters"][cid]["location"] != place:
                if action.fail_log:
                    self._system(format_log(action.fail_log, self._ctx(actor=cid, target=target, params=params)))
                return
        if "amount" in action.params:
            limits = (action.limits or {}).get("amount", {})
            amount = decision.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int):
                amount = limits.get("default")
            if amount is not None:
                if "min" in limits:
                    amount = max(limits["min"], amount)
                if "max" in limits:
                    amount = min(limits["max"], amount)
            params["amount"] = amount

        ctx = self._ctx(actor=cid, target=target, params=params)
        if not eval_conditions(action.requires, ctx):
            if action.fail_log:
                self._system(format_log(action.fail_log, ctx))
            return
        self._apply(action.effects, ctx, f"action {action_id} by {cid}")
        if place is not None:
            self.state["characters"][cid]["stats"]["intruder"] = place
            self._catch_burglars()

    def _break_place(self, cid, action, target):
        """Where a break_into action goes: the target's home, or the named location."""
        if action.break_into == "target":
            return self.characters[target].home if target in self.characters else None
        return action.break_into

    def _owner(self, lid, cid):
        """Whose permission `cid` needs to come into `lid` (a location's owner, else whose home it is), if anyone's."""
        owner = self.world.locations[lid].owner
        if owner:
            return owner if owner != cid else None
        return next((o for o, c in self.characters.items() if c.home == lid and o != cid), None)

    def _catch_burglars(self):
        """Anyone awake in the room with a burglar who is still inside catches them. A guard makes them wanted at once;
        anyone else may report them to the guard (report_burglary). A burglar who has left can't be caught."""
        for bid, bs in self.state["characters"].items():
            place = bs["stats"].get("intruder")
            if not place:
                continue
            if bs["location"] != place:
                bs["stats"]["intruder"] = ""
                continue
            name = self.world.locations[place].name
            for wid in self.order:
                chars = self.state["characters"]
                if wid == bid or chars[wid]["location"] != place or self._asleep(wid):
                    continue
                if self.characters[wid].guard:
                    effects = [
                        {"set": {"path": "target.intruder", "value": ""}},
                        {"set": {"path": "target.burglar_wanted", "value": True}},
                        {"say": {"who": "actor", "text": f"{{target}}, I caught you breaking into {name}. We need to talk."}},
                        {"log": f"Crime: {{actor}} caught {{target}} breaking into {name}."},
                    ]
                else:
                    effects = [
                        {"set": {"path": "target.intruder", "value": ""}},
                        {"add": {"path": "actor.saw_burglary.target", "amount": 1}},
                        {"log": f"Crime: {{actor}} caught {{target}} breaking into {name}."},
                        {"remember": {"who": "actor", "text": f"I caught {{target}} breaking into {name}. "
                                                              f"I can report them to Greg (report_burglary)."}},
                        {"remember": {"who": "target", "text": f"{{actor}} caught me breaking into {name}."}},
                    ]
                self._apply(effects, self._ctx(actor=wid, target=bid), f"caught {bid} by {wid}")
                break  # caught once per break-in

    def _run_events(self, phase):
        # A restart mid-slot (Ctrl+C saves state) re-runs the slot, so events that already ran are skipped.
        if phase in self.state.get("events_done", []):
            return
        for event in self.rules.events:
            if event.phase != phase:
                continue
            if event.when.slots is not None and self.slot not in event.when.slots:
                continue
            if event.for_each == "global":
                actors = [None]
            else:
                stat = event.for_each[len("stat:"):]
                actors = [cid for cid in self.order if stat in self.state["characters"][cid]["stats"]]
            for actor in actors:
                ctx = self._ctx(actor=actor)
                for branch in event.branches:
                    if eval_conditions(branch.if_, ctx):
                        self._apply(branch.effects, ctx, f"event {event.id} for {actor}")
                        break
        self.state.setdefault("events_done", []).append(phase)

    # ---- public interface ----------------------------------------------

    def _walk_seconds(self, moves):
        """Rough time for sprites to walk between rooms: tiles between rect centres (+4 for door detours)
        times clock.walk_seconds_per_tile; the longest of the given (from_id, to_id) moves."""
        def centre(lid):
            r = self.world.locations[lid].rect
            return r.x + r.width / 2, r.y + r.height / 2

        longest = 0.0
        for a, b in moves:
            if a == b or a not in self.world.locations or b not in self.world.locations:
                continue
            (ax, ay), (bx, by) = centre(a), centre(b)
            longest = max(longest, abs(ax - bx) + abs(ay - by) + 4)
        return longest * self.world.clock.walk_seconds_per_tile

    async def _walk_to(self, cid, dest, knock=True):
        """Walk `cid` to `dest` if they can move, there is room, and (for someone else's home) the owner lets them
        in (`knock=False`: a break-in, no asking). Waits for the walk, so they arrive before they talk and act."""
        here = self.state["characters"][cid]["location"]
        char = self.characters[cid]
        if dest not in self.world.locations or not (char.can_move or self.slot in char.off_duty) or dest == here:
            return
        if self._jailed(cid):
            self._system(f"{self.names[cid]} is locked in the jail cell.")
            return
        if self.slot in self.world.locations[dest].closed_slots:
            self._system(f"{self.names[cid]} finds {self.world.locations[dest].name} closed.")
            return
        if any(c.home == dest and self.state["characters"][o]["stats"].get("house") == BANK_OWNED
               for o, c in self.characters.items()):
            self._system(f"{self.names[cid]} finds {self.world.locations[dest].name} locked. The bank owns it now.")
            return
        rect = self.world.locations[dest].rect
        occupancy = sum(1 for cs in self.state["characters"].values() if cs["location"] == dest)
        if occupancy >= rect.width * rect.height or (knock and not await self._let_in(cid, dest)):
            return
        self.state["characters"][cid]["location"] = dest
        self.state["characters"][cid]["spot"] = None
        self.hub.publish({"type": "move", "character": cid, "to": dest})
        await self._sleep(self._walk_seconds([(here, dest)]))

    async def _let_in(self, cid, dest):
        """Someone else's home or owned place: knock. Nobody there, or the owner (asked through the LLM) says no -> stay out."""
        owner = self._owner(dest, cid)
        if owner is None or self.characters[cid].guard:  # guards walk straight in
            return True
        place =self.world.locations[dest].name
        if self.state["characters"][owner]["location"] != dest:
            self._system(f"{self.names[cid]} knocks on the door of {place}. No one is home.")
            return False
        if self._asleep(owner):
            self._system(f"{self.names[cid]} knocks on the door of {place}. No one answers.")
            return False
        self._system(f"{self.names[cid]} knocks on the door of {place}.")
        if not self.budget.allow():
            return False
        char = self.characters[owner]
        reply = await self.client.generate_turn(
            char=char, slot=self.slot, prompt=build_knock_prompt(char, self.names[cid], place),
            model=char.model or self.world.default_model,
        )
        self.budget.record()
        say = (reply.get("say") or "").strip()[:280]
        if say:
            self._speech(owner, say, cid if self.settings.all_llm else None)
        return reply.get("action") == "let_in"

    async def _take_spot(self, cid, spot):
        """One person per service spot (bank teller window, ATM; the grocery checkout has 2 lanes) in a room. If
        every lane is held, `cid` waits first, then the lane-1 holder steps away. Then `cid` walks up to it."""
        chars = self.state["characters"]
        here = chars[cid]["location"]
        lanes = [f"{spot}_{n}" for n in range(1, CHECKOUT_LANES + 1)] if spot == "checkout" else [spot]
        held = {cs.get("spot") for o, cs in chars.items() if o != cid and cs["location"] == here}
        lane = next((s for s in lanes if s not in held), None)
        if lane is None:
            lane = lanes[0]
            holder = next(o for o, cs in chars.items() if o != cid and cs.get("spot") == lane and cs["location"] == here)
            chars[cid]["spot"] = "wait"
            self._publish_state()
            await self._sleep(self.world.clock.turn_gap_seconds)
            chars[holder]["spot"] = None
        chars[cid]["spot"] = lane
        self._publish_state()
        await self._sleep(3 * self.world.clock.walk_seconds_per_tile)

    async def _patrol(self):
        """Guards walk up to everyone breaking a rule (rules.violations) and tell them off, face to face."""
        self._catch_burglars()
        for gid in self.order:
            guard = self.characters[gid]
            if not guard.guard:
                continue
            for cid in self.order:
                if cid == gid or self._asleep(cid):
                    continue
                ctx = self._ctx(actor=cid)
                found = [v for v in self.rules.violations if eval_conditions(v.if_, ctx)]
                if not found:
                    continue
                where = self.state["characters"][cid]["location"]
                await self._walk_to(gid, where)
                say = ""
                if self.budget.allow():
                    reply = await self.client.generate_turn(
                        char=guard, slot=self.slot,
                        prompt=build_confront_prompt(guard, self.names[cid], self.world.locations[where].name,
                                                     [v.text for v in found]),
                        model=guard.model or self.world.default_model,
                    )
                    self.budget.record()
                    say = (reply.get("say") or "").strip()[:280]
                if not say:
                    say = " ".join(format_log(v.say, ctx) for v in found)[:280]
                self._speech(gid, say, cid)
                talk = await self._converse(gid, cid, say)
                chars = self.state["characters"]
                for v in found:
                    if chars[gid]["location"] != chars[cid]["location"]:
                        continue  # e.g. an arrest only once the guard has reached them
                    effects = v.effects
                    if v.verdict:
                        effects = v.verdict.jail if await self._verdict(gid, cid, v, talk) else v.verdict.warn
                    if not effects:
                        continue
                    before = chars[cid]["location"]
                    self._apply(effects, self._ctx(actor=cid), f"violation {v.id} for {cid}")
                    chars = self.state["characters"]
                    if chars[cid]["location"] != before:  # taken somewhere (the jail cell): the guard walks along
                        self._publish_state()
                        await self._walk_to(gid, chars[cid]["location"])
                await self._sleep(self.world.clock.turn_gap_seconds)
            if self.rules.aid:
                await self._aid(gid)

    async def _verdict(self, gid, cid, v, talk):
        """True = jail. With an AI running, the guard's LLM decides after hearing them out; otherwise (mock mode, the
        call cap, or no clear answer) the fixed rule: jail when any of verdict.jail_if holds."""
        if not isinstance(self.client, MockClient) and self.budget.allow():
            guard = self.characters[gid]
            reply = await self.client.generate_turn(
                char=guard, slot=self.slot,
                prompt=build_verdict_prompt(guard, self.names[cid], v.text, talk,
                                            self.state["characters"][cid]["stats"]),
                model=guard.model or self.world.default_model,
            )
            self.budget.record()
            if reply.get("action") in ("jail", "warn"):
                say = (reply.get("say") or "").strip()[:280]
                if say:
                    self._speech(gid, say, cid)
                return reply["action"] == "jail"
        ctx = self._ctx(actor=cid)
        return any(eval_conditions(conds, ctx) for conds in v.verdict.jail_if)

    async def _aid(self, gid):
        """The guard finds someone to help each awake person matching rules.aid (e.g. health almost zero): walks up
        to a helper and asks; the helper walks over and helps (aid.effects)."""
        guard, aid = self.characters[gid], self.rules.aid
        for cid in self.order:
            if cid == gid or self.characters[cid].guard or self._asleep(cid):
                continue
            if not eval_conditions(aid.if_, self._ctx(actor=cid)):
                continue
            helper = next((o for o in self.order
                           if o not in (cid, gid) and not self.characters[o].guard and self.characters[o].can_move
                           and not self._asleep(o) and not self._jailed(o)
                           and not eval_conditions(aid.if_, self._ctx(actor=o))), None)
            if helper is None:
                self._system(f"{self.names[gid]} finds no one to help {self.names[cid]}.")
                continue
            chars = self.state["characters"]
            await self._walk_to(gid, chars[helper]["location"])
            ctx = self._ctx(actor=helper, target=cid)
            say = ""
            if self.budget.allow():
                reply = await self.client.generate_turn(
                    char=guard, slot=self.slot,
                    prompt=build_aid_prompt(guard, self.names[helper], self.names[cid],
                                            self.world.locations[chars[cid]["location"]].name),
                    model=guard.model or self.world.default_model,
                )
                self.budget.record()
                say = (reply.get("say") or "").strip()[:280]
            self._speech(gid, say or format_log(aid.say, ctx)[:280], helper)
            await self._walk_to(helper, chars[cid]["location"])
            if self.state["characters"][helper]["location"] == self.state["characters"][cid]["location"]:
                self._apply(aid.effects, self._ctx(actor=helper, target=cid), f"aid by {helper} for {cid}")
            await self._sleep(self.world.clock.turn_gap_seconds)

    async def _converse(self, cid, to, said):
        """`cid` just said `said` to `to` face to face. They take turns answering until one ends the talk."""
        chars = self.state["characters"]
        talk = [(self.names[cid], said)]
        speaker, listener = to, cid
        # The browser keeps the one spoken to standing still while the talk is open.
        self.hub.publish({"type": "talk", "pair": [cid, to], "open": True})
        try:
            for _ in range(TALK_MAX):
                if (self._asleep(speaker) or chars[speaker]["location"] != chars[listener]["location"]
                        or not self.budget.allow()):
                    return talk
                char = self.characters[speaker]
                place = self.world.locations[chars[speaker]["location"]].name
                reply = await self.client.generate_turn(
                    char=char, slot=self.slot,
                    prompt=build_reply_prompt(char, self.names[listener], place, talk, chars[speaker]["stats"]),
                    model=char.model or self.world.default_model,
                )
                self.budget.record()
                say = (reply.get("say") or "").strip()[:280]
                if not say:
                    return talk
                self._speech(speaker, say, listener)
                talk.append((self.names[speaker], say))
                if reply.get("action") != "continue":
                    return talk
                await self._sleep(self.world.clock.turn_gap_seconds)
                speaker, listener = listener, speaker
            return talk
        finally:
            self.hub.publish({"type": "talk", "pair": [cid, to], "open": False})

    async def _rounds(self, lid):
        """A landlord walks up to each awake tenant (anyone with the rounds stat), one by one, and talks with them
        face to face."""
        boss = self.characters[lid]
        chars = self.state["characters"]
        for cid in self.order:
            stats = chars[cid]["stats"]
            if cid == lid or boss.rounds.stat not in stats or self._asleep(cid):
                continue
            where = chars[cid]["location"]
            await self._walk_to(lid, where)
            if chars[lid]["location"] != where:
                continue  # closed, full, or not let in
            if not self.budget.allow():
                return
            reply = await self.client.generate_turn(
                char=boss, slot=self.slot,
                prompt=build_rounds_prompt(boss, self.names[cid], self.world.locations[where].name,
                                           boss.rounds.stat, stats[boss.rounds.stat]),
                model=boss.model or self.world.default_model,
            )
            self.budget.record()
            say = (reply.get("say") or "").strip()[:280]
            if not say:
                continue
            self._speech(lid, say, cid)
            await self._converse(lid, cid, say)
            await self._sleep(self.world.clock.turn_gap_seconds)

    async def run_slot(self) -> None:
        slot = self.slot
        self.hub.publish({"type": "slot_start", "day": self.state["clock"]["day"], "slot": slot})

        # Everyone heads to their scheduled room; the first turn waits until they have walked there.
        moves = []
        for cid, char in self.characters.items():
            before = self.state["characters"][cid]["location"]
            dest = char.schedule.get(slot) or char.home
            jailed_or_home = dest == char.home or self._jailed(cid)  # the jailed stay in the cell
            self.state["characters"][cid]["location"] = self._home(cid) if jailed_or_home else dest
            self.state["characters"][cid]["spot"] = None
            moves.append((before, self.state["characters"][cid]["location"]))
        self._publish_state()
        await self._sleep(self._walk_seconds(moves))

        deferred = []
        for i, cid in enumerate(self.order):
            char = self.characters[cid]
            if char.guard and not self.settings.guard_turns:
                continue  # guards only speak on patrol, about real rule breaking
            if char.rounds and char.rounds.slot == slot:
                await self._rounds(cid)
                continue  # the rounds replace this turn
            decision, used_llm = await self._decide(char)

            await self._walk_to(cid, decision.get("move_to"))

            say = (decision.get("say") or "").strip()[:280]
            action = self.rules.actions.get(decision.get("action") or "none")
            meet = None  # an action done face to face (punch, bail, complain to the guard): walk up to them first
            if (action is not None and action.meet and decision.get("action") in char.allowed_actions
                    and (not action.only_slots or slot in action.only_slots)):
                meet = decision.get("target") if action.meet == "target" else action.meet
                if meet not in self.characters or meet == cid:
                    meet = None
            # Local mode: talking to someone means walking up to them first (the browser steps them side by side).
            to = meet or decision.get("target")
            if to in self.characters and to != cid and self._asleep(to):
                say = ""  # nobody talks to a sleeper
            breaking = (action is not None and action.break_into and decision.get("action") in char.allowed_actions
                        and (not action.only_slots or slot in action.only_slots))
            if breaking or not (self.settings.all_llm and say and to in self.characters and to != cid):
                to = None  # a break-in's target is whose place it is, not someone to walk up to
            if meet or to:
                await self._walk_to(cid, self.state["characters"][meet or to]["location"])
            if breaking:
                place = self._break_place(cid, action, decision.get("target"))
                if place and self._owner(place, cid):
                    await self._walk_to(cid, place, knock=False)  # burglary: straight in, no knock
            if (action is not None and action.spot and decision.get("action") in char.allowed_actions
                    and (not action.only_slots or slot in action.only_slots)):  # no queue at a closed bank
                await self._take_spot(cid, action.spot)  # queue for the teller window / ATM, then step up
            if action is not None and action.timing == "deferred" and decision.get("action") in char.allowed_actions:
                deferred.append((cid, decision, say, to))  # spoken when the action happens
            else:
                if say and not self._alone(cid):
                    self._speech(cid, say, to)
                    if to:
                        await self._converse(cid, to, say)
                self._resolve_action(cid, decision)
            self._catch_burglars()  # someone may have walked in on a burglar

            if used_llm and i < len(self.order) - 1:
                await self._sleep(self.world.clock.turn_gap_seconds)

        self._run_events("main")
        for cid, decision, say, to in deferred:
            before = self.state["characters"][cid]["location"]
            self._resolve_action(cid, decision)
            self._catch_burglars()
            after = self.state["characters"][cid]["location"]
            if after != before:  # the action walked them somewhere (e.g. drink -> tavern)
                self._publish_state()
                await self._sleep(self._walk_seconds([(before, after)]))
            if say and not self._alone(cid):
                self._speech(cid, say, to)
                if to:
                    await self._converse(cid, to, say)
        await self._patrol()
        self._run_events("cleanup")

        if slot == self.world.clock.slots[-1]:
            # Bedtime: after the last slot everyone walks to their own home, then sleeps there for
            # clock.sleep_seconds while it is still Night (the browser draws them in bed), before the next day.
            moves = []
            for cid in self.characters:
                before = self.state["characters"][cid]["location"]
                home = self._home(cid)
                self.state["characters"][cid]["location"] = home
                self.state["characters"][cid]["spot"] = None
                moves.append((before, home))
            self._publish_state()
            await self._sleep(self._walk_seconds(moves) + self.world.clock.sleep_seconds)

        clock = self.state["clock"]
        clock["slot_index"] += 1
        if clock["slot_index"] >= len(self.world.clock.slots):
            clock["slot_index"] = 0
            clock["day"] += 1
        self.state["events_done"] = []

        self.save()
        self._publish_state()
        await self._sleep(self.world.clock.slot_gap_seconds)

    async def run_forever(self) -> None:
        while True:
            if self._reset_requested:
                self._reset_requested = False
                for path in (self.state_path, self.chat_path):
                    if path.exists():
                        path.unlink()
                self.state = initial_state(self.world, self.characters)
                self.hub.publish(self.snapshot())
            if self.paused or (not self.settings.always_run and self.hub.viewers == 0):
                await asyncio.sleep(0.5)
                continue
            try:
                await self.run_slot()
            except Exception:
                logger.exception("slot failed for world %s", self.world.id)
                await self._sleep(5)

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "state": self._public_state(),
            "log": self.state["log"][-SNAPSHOT_LOG:],
            "paused": self.paused,
        }

    def pause(self) -> None:
        self.paused = True
        self.hub.publish({"type": "notice", "level": "info", "text": "Simulation paused."})

    def resume(self) -> None:
        self.paused = False
        self.hub.publish({"type": "notice", "level": "info", "text": "Simulation resumed."})

    def request_reset(self) -> None:
        self._reset_requested = True
