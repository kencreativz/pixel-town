from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MapSpec(BaseModel):
    width: int
    height: int
    tile_size: int


class ClockSpec(BaseModel):
    slots: List[str]
    turn_gap_seconds: float
    slot_gap_seconds: float


class Rect(BaseModel):
    x: int
    y: int
    width: int
    height: int


class Location(BaseModel):
    name: str
    type: Literal["office", "workshop", "tavern", "home", "plaza"]
    color: str
    rect: Rect


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


class Sprite(BaseModel):
    style: Optional[str] = None
    skin: Optional[str] = None
    hair: Optional[str] = None
    outfit: Optional[str] = None
    accessory: Optional[str] = None


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


class Rules(BaseModel):
    actions: Dict[str, Action]
    events: List[Event]
