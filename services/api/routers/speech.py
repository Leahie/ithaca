"""Speech through Deepgram: the guide's voice (Aura) and push-to-talk transcription (Nova).

POST /api/speech/tts: the browser posts text (and voice: female | male) and plays the MP3 it gets back.
WS /ws/speech/listen: the browser streams microphone audio (16-bit PCM, mono, at
?sample_rate=) and gets back {"text", "final"} with the whole transcript so far; it sends
{"type": "stop"} on release, and gets one message with final: true before the socket closes.
With ?auto=1 the server ends it by itself when the speaker pauses (or after 8 s of nothing).

The key stays on the server (I-G22). Without DEEPGRAM_API_KEY the route answers 503, the socket
closes with code 4503, and the web app uses the browser's own speech.
"""

from __future__ import annotations

import array
import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from websockets.asyncio.client import connect

from services.api.integrations.config import REPO_ROOT, env
from services.api.routers.speech_text import for_speech

log = logging.getLogger(__name__)
router = APIRouter(tags=["speech"])

DEEPGRAM_SPEAK = "https://api.deepgram.com/v1/speak"
DEEPGRAM_LISTEN = "wss://api.deepgram.com/v1/listen"
DEFAULT_VOICE = "aura-2-thalia-en"
DEFAULT_MALE_VOICE = "aura-2-orion-en"
DEFAULT_STT_MODEL = "nova-3"
MAX_CHARS = 2000  # Deepgram's limit for one request
NOT_CONFIGURED = 4503


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    voice: Literal["female", "male"] = "female"


def voice_model(voice: str) -> str:
    if voice == "male":
        return env("DEEPGRAM_TTS_MODEL_MALE") or DEFAULT_MALE_VOICE
    return env("DEEPGRAM_TTS_MODEL") or DEFAULT_VOICE


def tts_provider() -> str | None:
    return "deepgram" if env("DEEPGRAM_API_KEY") else None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


@router.post("/speech/tts", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def text_to_speech(body: SpeakRequest) -> Response:
    key = env("DEEPGRAM_API_KEY")
    if not key:
        raise HTTPException(503, "text to speech is not configured: set DEEPGRAM_API_KEY")
    try:
        async with _client() as http:
            r = await http.post(
                DEEPGRAM_SPEAK,
                params={"model": voice_model(body.voice), "encoding": "mp3"},
                headers={"Authorization": f"Token {key}"},
                json={"text": for_speech(body.text)[:MAX_CHARS]},
            )
    except httpx.HTTPError as err:
        raise HTTPException(502, f"Deepgram did not answer: {err}") from err
    if r.status_code != 200:
        raise HTTPException(502, f"Deepgram answered {r.status_code}: {r.text[:300]}")
    return Response(r.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


# Hands-free listening (?auto=1): Deepgram marks the end of speech after this much silence, and
# the socket then ends on its own, without waiting for the browser to say stop.
ENDPOINT_SILENCE_MS = 900
UTTERANCE_END_MS = 1400
NOTHING_HEARD_S = 8.0  # give up if the microphone opened and nobody spoke


def _deepgram_listen(sample_rate: int, key: str, auto: bool = False):
    params = {
        "model": env("DEEPGRAM_STT_MODEL") or DEFAULT_STT_MODEL,
        "language": env("DEEPGRAM_STT_LANGUAGE") or "en",
        "encoding": "linear16",
        "sample_rate": sample_rate,
        "channels": 1,
        "smart_format": "true",
        "interim_results": "true",
    }
    if auto:
        params |= {"endpointing": ENDPOINT_SILENCE_MS, "utterance_end_ms": UTTERANCE_END_MS, "vad_events": "true"}
    return connect(f"{DEEPGRAM_LISTEN}?{urlencode(params)}", additional_headers={"Authorization": f"Token {key}"})


async def listen_socket(ws: WebSocket, sample_rate: int = 48000, auto: bool = False) -> None:
    await ws.accept()
    started = time.monotonic()
    key = env("DEEPGRAM_API_KEY")
    if not key:
        await ws.close(code=NOT_CONFIGURED, reason="speech to text is not configured: set DEEPGRAM_API_KEY")
        return
    if not 8000 <= sample_rate <= 96000:
        await ws.close(code=1003, reason="sample_rate must be 8000-96000")
        return

    # What happened on this socket, one line when it ends (data/agent/speech.log): how much audio
    # arrived, how loud it was, what Deepgram sent, and what the browser said about its microphone.
    seen = {"rate": sample_rate, "auto": auto, "frames": 0, "bytes": 0, "peak": 0, "dg": {}, "client": None, "end": "?"}

    finals: list[str] = []
    interim = ""
    gone = False  # the browser closed the socket: nothing more to send it

    def transcript() -> str:
        return " ".join(t for t in [*finals, interim] if t).strip()

    try:
        opened = time.monotonic()
        async with _deepgram_listen(sample_rate, key, auto) as dg:

            async def relay_audio() -> None:
                nonlocal gone
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            gone = True
                            break
                        if msg.get("bytes"):
                            data = msg["bytes"]
                            seen["frames"] += 1
                            seen["bytes"] += len(data)
                            if seen["frames"] % 5 == 1 and len(data) >= 2:
                                pcm = array.array("h", data[: len(data) // 2 * 2])
                                seen["peak"] = max(seen["peak"], max(abs(v) for v in pcm[::16]))
                            await dg.send(data)
                        elif msg.get("text"):
                            said = json.loads(msg["text"])
                            if said.get("type") == "diag":
                                seen["client"] = said.get("facts")
                            elif said.get("type") == "stop":
                                seen["end"] = "browser said stop"
                                break
                finally:
                    # Deepgram sends what it still holds, then closes, which ends the loop below.
                    with suppress(Exception):
                        await dg.send(json.dumps({"type": "CloseStream"}))

            audio = asyncio.create_task(relay_audio())
            try:
                async for raw in dg:
                    data = json.loads(raw)
                    seen["dg"][data.get("type", "?")] = seen["dg"].get(data.get("type", "?"), 0) + 1
                    if auto and data.get("type") == "UtteranceEnd" and transcript():
                        seen["end"] = "utterance end"
                        break  # they stopped talking
                    if data.get("type") != "Results":
                        continue
                    text = data["channel"]["alternatives"][0]["transcript"].strip()
                    if data.get("is_final"):
                        if text:
                            finals.append(text)
                        interim = ""
                    else:
                        interim = text
                    if not gone:
                        await ws.send_json({"text": transcript(), "final": False})
                    if auto and data.get("speech_final") and transcript():
                        seen["end"] = "pause after speech"
                        break  # a pause after speech: that was the whole question
                    if auto and not transcript() and time.monotonic() - opened > NOTHING_HEARD_S:
                        seen["end"] = "nothing heard"
                        break  # nobody spoke
            finally:
                audio.cancel()
        if not gone:
            await ws.send_json({"text": transcript(), "final": True})
            await ws.close()
    except WebSocketDisconnect:
        seen["end"] = "browser left"
    except Exception as err:  # Deepgram refused or dropped the stream
        seen["end"] = f"failed: {type(err).__name__}: {err}"[:200]
        log.warning("speech to text failed: %s", err)
        with suppress(Exception):
            await ws.close(code=1011, reason=f"speech to text failed: {err}"[:120])
    finally:
        seen["heard"] = transcript()
        seen["seconds"] = round(time.monotonic() - started, 1)
        with suppress(Exception):
            path = REPO_ROOT / "data" / "agent" / "speech.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + json.dumps(seen) + "\n")
