import asyncio
import json
import os
import re
import time
import urllib.request
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
                if getattr(exc, "code", None) in (400, 401, 403, 404):  # bad request/key/model: retrying won't help
                    break
        return dict(NONE_TURN)


class OllamaClient:
    """Local model through Ollama (http://localhost:11434). Model from OLLAMA_MODEL; the world's model id is ignored."""

    def __init__(self):
        self.model = os.environ.get("OLLAMA_MODEL", "ssfdre38/gemma4-turbo")

    def _post(self, body):
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read())

    async def generate_turn(self, *, char, slot, prompt, model):
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "format": TurnSchema.model_json_schema(),
            "stream": False,
            "options": {"temperature": char.temperature},
        }
        try:
            res = await asyncio.to_thread(self._post, body)
            return parse_turn(res["message"]["content"])
        except Exception as exc:  # Ollama not running, model missing, timeout...
            print(f"[llm] {char.id}: ollama failed: {exc}")
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
    if mode == "ollama":
        return OllamaClient()
    return GeminiClient()
