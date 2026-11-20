from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Rect(BaseModel):
    x: int
    y: int
    width: int
    height: int


class MapSpec(BaseModel):
    width: int
    height: int
    tile_size: int
    roads: List[Rect] = []  # the only walkable tiles outside rooms


class ClockSpec(BaseModel):
    slots: List[str]
    turn_gap_seconds: float
    slot_gap_seconds: float
    walk_seconds_per_tile: float = 0.0  # wait for sprites to walk before talking/acting; 0 = no wait
    sleep_seconds: float = 0.0  # time asleep at home after the last slot, before the next day


class Location(BaseModel):
    name: str
    type: Literal["office", "workshop", "studio", "tavern", "home", "plaza", "bank", "jail", "alley", "shop"]
    color: str
    rect: Rect
    closed_slots: List[str] = []  # slots when nobody may walk in
    owner: Optional[str] = None  # a character whose permission is needed to come in (homes: their owner)


class World(BaseModel):
    id: str
    name: str
    seed: int
    map: MapSpec
    clock: ClockSpec
    history_lines: int
    default_model: str
    turn_order: List[str]
    globals: Dict[str, Any]
    locations: Dict[str, Location]
    shelter: Optional[str] = None  # where people sleep once the bank owns their home
    jail: Optional[str] = None  # where the guard locks up arrested people


class Sprite(BaseModel):
    style: Optional[str] = None
    skin: Optional[str] = None
    hair: Optional[str] = None
    outfit: Optional[str] = None
    accessory: Optional[str] = None


class Rounds(BaseModel):
    stat: str  # tenants: everyone with this stat
    slot: str  # the slot the rounds replace the normal turn


class Character(BaseModel):
    id: str
    name: str
    kind: Literal["llm", "scripted", "hybrid"]
    sprite: Sprite
    home: str
    schedule: Dict[str, str]
    stats: Dict[str, Any]
    public_stats: List[str]
    allowed_actions: List[str]
    can_move: bool = False
    model: Optional[str] = None
    temperature: float = 1.0
    persona: Optional[str] = None
    lines: Dict[str, List[str]] = {}
    talk_chance: float = 1.0
    mock_plan: Dict[str, Dict[str, Any]] = {}
    guard: bool = False  # skips normal turns; walks up to rule breakers (rules.violations) and tells them off
    local_stats: Dict[str, Any] = {}  # extra stats merged in only in local (all_llm) mode
    rounds: Optional[Rounds] = None  # walks up to each awake tenant once a day and talks, face to face
    off_duty: Dict[str, Dict[str, Any]] = {}  # slot -> scripted plan; may walk in these slots even without can_move

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind in ("llm", "hybrid") and not self.persona:
            raise ValueError(f"kind '{self.kind}' requires 'persona'")
        if self.kind == "scripted" and not self.lines:
            raise ValueError("kind 'scripted' requires 'lines'")
        return self


class Action(BaseModel):
    description: str
    timing: Literal["immediate", "deferred"]
    only_slots: Optional[List[str]] = None
    params: List[str]
    limits: Optional[Dict[str, Dict[str, int]]] = None
    requires: Optional[List[Dict[str, Any]]] = None
    effects: List[Dict[str, Any]]
    fail_log: Optional[str] = None
    spot: Optional[Literal["teller", "atm", "checkout"]] = None  # a service spot the actor must take first
    meet: Optional[str] = None  # "target" or a character id: the actor walks to them before acting
    # "target" (the target's home) or a location id with an owner: the actor walks in without knocking (burglary)
    break_into: Optional[str] = None


class EventWhen(BaseModel):
    slots: Optional[List[str]] = None


class Branch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    if_: Optional[List[Dict[str, Any]]] = Field(default=None, alias="if")
    effects: List[Dict[str, Any]]


class Event(BaseModel):
    id: str
    when: EventWhen
    phase: Literal["main", "cleanup"]
    for_each: str
    branches: List[Branch]


class Verdict(BaseModel):
    jail_if: List[List[Dict[str, Any]]] = []  # the fixed rule: jail when any of these condition lists holds
    jail: List[Dict[str, Any]]  # effects (actor = the offender)
    warn: List[Dict[str, Any]]


class Violation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    text: str  # what the guard is told the person did
    say: str  # the guard's line when no LLM line is available
    if_: List[Dict[str, Any]] = Field(alias="if")
    effects: List[Dict[str, Any]] = []  # applied after the guard confronts them (actor = the offender)
    verdict: Optional[Verdict] = None  # after hearing them out, the guard jails or warns them


class Aid(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    if_: List[Dict[str, Any]] = Field(alias="if")  # who needs help (actor = the patient)
    say: str  # the guard's line to the helper when no LLM line is available (actor = helper, target = patient)
    effects: List[Dict[str, Any]]  # the help itself (actor = helper, target = patient)


class Rules(BaseModel):
    actions: Dict[str, Action]
    events: List[Event]
    violations: List[Violation] = []
    aid: Optional[Aid] = None  # guards send a helper to anyone matching aid.if
