import asyncio
import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path

from . import npc
from .dsl import Context, DSLError, apply_effects, eval_conditions, format_log
from .llm import CallBudget
from .loader import initial_state, reconcile_state
from .prompts import build_prompt

LOG_KEEP = 200
SNAPSHOT_LOG = 50
CAP_NOTICE = "LLM call cap reached; characters are quiet until the window resets."


@dataclass
class Settings:
    time_scale: float
    always_run: bool
    max_calls_per_hour: int


class Simulation:
    def __init__(self, world, rules, characters, llm_client, runtime_dir, settings, hub):
        self.world = world
        self.rules = rules
        self.characters = characters
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
        log.append({"seq": seq, "speaker": speaker, "name": name, "text": text,
                    "day": self.state["clock"]["day"], "slot": self.slot})
        del log[:-LOG_KEEP]
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.chat_path, "a", encoding="utf-8") as f:
            f.write(f"[{name}]: {text}\n")

    def _speech(self, cid, text):
        name = self.names[cid]
        self._add_log(cid, name, text)
        self.hub.publish({"type": "speech", "speaker": cid, "name": name, "text": text})

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
            print(f"[rules] {label}: {exc}")
            return
        self.state = new_state
        for line in logs:
            self._system(line)

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_path)

    # ---- decisions -----------------------------------------------------

    async def _decide(self, char):
        """Returns (decision, used_llm)."""
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

    def _run_events(self, phase):
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

    # ---- public interface ----------------------------------------------

    async def run_slot(self) -> None:
        slot = self.slot
        self.hub.publish({"type": "slot_start", "day": self.state["clock"]["day"], "slot": slot})

        for cid, char in self.characters.items():
            self.state["characters"][cid]["location"] = char.schedule.get(slot) or char.home
        self._publish_state()

        deferred = []
        for i, cid in enumerate(self.order):
            char = self.characters[cid]
            decision, used_llm = await self._decide(char)

            dest = decision.get("move_to")
            here = self.state["characters"][cid]["location"]
            if dest in self.world.locations and char.can_move and dest != here:
                rect = self.world.locations[dest].rect
                occupancy = sum(1 for cs in self.state["characters"].values() if cs["location"] == dest)
                if occupancy < rect.width * rect.height:
                    self.state["characters"][cid]["location"] = dest
                    self.hub.publish({"type": "move", "character": cid, "to": dest})

            say = (decision.get("say") or "").strip()[:280]
            if say:
                self._speech(cid, say)

            action = self.rules.actions.get(decision.get("action") or "none")
            if action is not None and action.timing == "deferred" and decision.get("action") in char.allowed_actions:
                deferred.append((cid, decision))
            else:
                self._resolve_action(cid, decision)

            if used_llm and i < len(self.order) - 1:
                await self._sleep(self.world.clock.turn_gap_seconds)

        self._run_events("main")
        for cid, decision in deferred:
            self._resolve_action(cid, decision)
        self._run_events("cleanup")

        clock = self.state["clock"]
        clock["slot_index"] += 1
        if clock["slot_index"] >= len(self.world.clock.slots):
            clock["slot_index"] = 0
            clock["day"] += 1

        self._save()
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
                traceback.print_exc()
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

    def resume(self) -> None:
        self.paused = False

    def request_reset(self) -> None:
        self._reset_requested = True
