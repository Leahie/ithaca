"""POST /api/speech/tts and WS /ws/speech/listen against a fake Deepgram."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.api.agent import provider
from services.api.routers import speech


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("AGENT_STORAGE", "memory")
    provider._storage.cache_clear()
    from services.api.main import app

    with TestClient(app) as c:
        yield c
    provider._storage.cache_clear()


def fake_deepgram(monkeypatch, handler):
    monkeypatch.setattr(speech, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_speaks_through_deepgram_with_the_server_key(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-secret")
    monkeypatch.delenv("DEEPGRAM_TTS_MODEL", raising=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["model"] = request.url.params["model"]
        seen["body"] = request.content
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    fake_deepgram(monkeypatch, handler)
    r = client.post("/api/speech/tts", json={"text": "Welcome home."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"
    assert seen == {"auth": "Token dg-secret", "model": speech.DEFAULT_VOICE, "body": b'{"text":"Welcome home."}'}
    assert client.get("/api/health").json()["tts_provider"] == "deepgram"


def test_the_male_voice_uses_its_own_model(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-secret")
    monkeypatch.delenv("DEEPGRAM_TTS_MODEL_MALE", raising=False)
    models = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(request.url.params["model"])
        return httpx.Response(200, content=b"ID3")

    fake_deepgram(monkeypatch, handler)
    assert client.post("/api/speech/tts", json={"text": "Hi", "voice": "male"}).status_code == 200
    monkeypatch.setenv("DEEPGRAM_TTS_MODEL_MALE", "aura-2-zeus-en")
    assert client.post("/api/speech/tts", json={"text": "Hi", "voice": "male"}).status_code == 200
    assert models == [speech.DEFAULT_MALE_VOICE, "aura-2-zeus-en"]
    assert client.post("/api/speech/tts", json={"text": "Hi", "voice": "robot"}).status_code == 422


def test_without_a_key_the_browser_voice_takes_over(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    assert client.post("/api/speech/tts", json={"text": "Hello"}).status_code == 503
    assert client.get("/api/health").json()["tts_provider"] is None


def test_deepgram_errors_and_oversized_text(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-secret")
    fake_deepgram(monkeypatch, lambda request: httpx.Response(401, json={"err_msg": "bad key"}))
    r = client.post("/api/speech/tts", json={"text": "Hello"})
    assert r.status_code == 502 and "401" in r.json()["detail"]
    assert client.post("/api/speech/tts", json={"text": "x" * (speech.MAX_CHARS + 1)}).status_code == 422
    assert client.post("/api/speech/tts", json={"text": ""}).status_code == 422


def result(text: str, final: bool) -> dict:
    return {"type": "Results", "is_final": final, "channel": {"alternatives": [{"transcript": text}]}}


class FakeListen:
    """Deepgram's live socket: results while audio flows, the last words after CloseStream."""

    def __init__(self, during: list[dict], after: list[dict]):
        self.during, self.after = during, after
        self.audio: list[bytes] = []
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        if isinstance(data, bytes):
            self.audio.append(data)
        elif json.loads(data)["type"] == "CloseStream":
            self.closed.set()

    async def _messages(self):
        for m in [{"type": "Metadata"}, *self.during]:
            yield json.dumps(m)
        await self.closed.wait()
        for m in self.after:
            yield json.dumps(m)

    def __aiter__(self):
        return self._messages()


def test_listen_streams_the_whole_transcript_then_one_final(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-secret")
    fake = FakeListen(
        during=[result("open the", False), result("Open the kitchen.", True), result("and the", False)],
        after=[result("And the yard.", True), result("", True)],
    )
    opened = {}

    def listen(sample_rate, key):
        opened.update(sample_rate=sample_rate, key=key)
        return fake

    monkeypatch.setattr(speech, "_deepgram_listen", listen)
    with client.websocket_connect("/ws/speech/listen?sample_rate=16000") as ws:
        ws.send_bytes(b"\x01\x00" * 160)
        assert [ws.receive_json()["text"] for _ in range(3)] == ["open the", "Open the kitchen.", "Open the kitchen. and the"]
        ws.send_text(json.dumps({"type": "stop"}))
        messages = []
        while not messages or not messages[-1]["final"]:
            messages.append(ws.receive_json())
    assert messages[-1] == {"text": "Open the kitchen. And the yard.", "final": True}
    assert opened == {"sample_rate": 16000, "key": "dg-secret"}
    assert fake.audio == [b"\x01\x00" * 160]


def test_listen_without_a_key_closes_so_the_browser_listens(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    with client.websocket_connect("/ws/speech/listen") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == speech.NOT_CONFIGURED
