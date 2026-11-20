import asyncio
import json
from types import SimpleNamespace

import engine.llm as llm
from engine.llm import NONE_TURN, GeminiClient, OllamaClient

CHAR = SimpleNamespace(id="bob", temperature=0.7)
TURN = {"say": "Hi", "action": "none", "target": None, "amount": None, "move_to": None}


def turn(client):
    return asyncio.run(client.generate_turn(char=CHAR, slot="Morning", prompt="p", model="gemini-x"))


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ollama_turn(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return FakeResponse({"message": {"content": json.dumps(TURN)}})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OLLAMA_MODEL", "gemma3:4b")
    assert turn(OllamaClient()) == TURN
    assert sent["model"] == "gemma3:4b"
    assert sent["stream"] is False
    assert "say" in sent["format"]["properties"]
    assert sent["options"] == {"temperature": 0.7}


def test_ollama_down(monkeypatch):
    def refuse(req, timeout):
        raise ConnectionRefusedError("no ollama")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    assert turn(OllamaClient()) == NONE_TURN


class CodedError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


def gemini_with_error(code):
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs["model"])
        raise CodedError(code)

    client = object.__new__(GeminiClient)  # skip __init__: no key, no network
    client._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    return client, calls


def test_gemini_404_not_retried():
    client, calls = gemini_with_error(404)
    assert turn(client) == NONE_TURN
    assert len(calls) == 1


def test_gemini_503_retried(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    client, calls = gemini_with_error(503)
    assert turn(client) == NONE_TURN
    assert len(calls) == 3
