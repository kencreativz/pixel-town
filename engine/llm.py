import asyncio
import json
import re
import time
from collections import deque
from typing import Optional

from pydantic import BaseModel


class TurnSchema(BaseModel):
    """Schema sent to Gemini (no defaults, which some SDK versions reject)."""
    say: str
    action: str
    target: Optional[str]
    amount: Optional[int]
    move_to: Optional[str]


class TurnOutput(BaseModel):
    """Lenient model used when parsing."""
    say: str = ""
    action: str = "none"
    target: Optional[str] = None
    amount: Optional[int] = None
    move_to: Optional[str] = None


NONE_TURN = {"say": "", "action": "none", "target": None, "amount": None, "move_to": None}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_turn(text):
    """Parse model output into a turn dict. Never raises; returns NONE_TURN on failure."""
    if not text:
        return dict(NONE_TURN)
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    candidates = [cleaned]
    match = _OBJ_RE.search(cleaned)
    if match:
        candidates.append(match.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                out = TurnOutput.model_validate(data).model_dump()
                out["say"] = (out["say"] or "").strip()[:280]
                out["action"] = ((out["action"] or "none").strip().lower()) or "none"
                return out
        except Exception:
            continue
    return dict(NONE_TURN)


class CallBudget:
    """Sliding one-hour window. max_per_hour <= 0 means unlimited."""

    def __init__(self, max_per_hour):
        self.max = max_per_hour
        self._stamps = deque()

    def _trim(self, now):
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()

    def allow(self):
        if self.max <= 0:
            return True
        self._trim(time.time())
        return len(self._stamps) < self.max

    def record(self):
        self._stamps.append(time.time())


class GeminiClient:
    def __init__(self):
        from google import genai  # imported lazily so mock mode needs no key
        self._client = genai.Client()  # reads GEMINI_API_KEY from the environment

    async def generate_turn(self, *, char, slot, prompt, model):
        from google.genai import types
        for delay in (0, 2, 5):
            if delay:
                await asyncio.sleep(delay)
            try:
                res = await self._client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TurnSchema,
                        temperature=char.temperature,
                    ),
                )
                return parse_turn(res.text)
            except Exception as exc:  # network, quota, safety, schema...
                print(f"[llm] {char.id}: attempt failed: {exc}")
        return dict(NONE_TURN)


class MockClient:
    """Deterministic, offline. Uses each character's mock_plan[slot]."""

    def __init__(self):
        self.calls = []  # list of (character_id, slot)

    async def generate_turn(self, *, char, slot, prompt, model):
        self.calls.append((char.id, slot))
        plan = (char.mock_plan or {}).get(slot, {})
        out = dict(NONE_TURN)
        out.update({k: v for k, v in plan.items() if k in out})
        return out


def make_client(mode):
    if mode == "mock":
        return MockClient()
    return GeminiClient()
