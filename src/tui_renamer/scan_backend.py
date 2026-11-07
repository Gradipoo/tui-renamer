"""Pluggable scan-back recognition.

The first backend talks to a local Ollama HTTP server that the user already
runs. Nothing is installed or started from this app. Image bytes are posted
only to the configured host. Model JSON is untrusted and strictly parsed.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image

from tui_renamer.scan_config import DEFAULT_HOST, valid_host
from tui_renamer.scan_dates import ScanDate, parse_model_dates
from tui_renamer.scan_notes import MAX_TEXT

MAX_IMAGE_EDGE = 1280
MAX_ENCODED = 4 * 1024 * 1024
MAX_RESPONSE = 1 * 1024 * 1024
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120

PROMPT = (
    "Transcribe handwritten or stamped text from this photograph back. "
    "Do not describe the scene on the front. Do not guess a decade or year "
    "from clothing, cars, or paper color. Do not invent a month or day when "
    "the writing only has a year. If a date is unclear, list each reading as "
    "its own dates entry and mark uncertain true. Return JSON only."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "transcription": {"type": "string"},
        "notes": {"type": "string"},
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["year", "month", "day", "season", "range"],
                    },
                    "text": {"type": "string"},
                    "year": {"type": ["integer", "null"]},
                    "month": {"type": ["integer", "null"]},
                    "day": {"type": ["integer", "null"]},
                    "season": {"type": ["string", "null"]},
                    "uncertain": {"type": "boolean"},
                },
                "required": ["kind", "text"],
            },
        },
    },
    "required": ["transcription", "dates"],
}


class BackendError(Exception):
    """User-facing recognition failure. The message is one line."""


@dataclass(frozen=True)
class Recognition:
    transcription: str
    notes: str
    dates: list[ScanDate]
    engine: str
    model: str
    raw: str


class ScanBackend(Protocol):
    def recognize(self, image: Path, *, model: str) -> Recognition:
        """Read a back image. Raise BackendError on failure."""


@dataclass
class MockBackend:
    """Deterministic backend for tests. Never opens the image as a model."""

    transcription: str = "June 1952 Aunt May"
    notes: str = ""
    dates: list[ScanDate] | None = None
    error: str | None = None

    def recognize(self, image: Path, *, model: str) -> Recognition:
        del image
        if self.error:
            raise BackendError(self.error)
        dates = (
            self.dates
            if self.dates is not None
            else [ScanDate("month", "June 1952", year=1952, month=6)]
        )
        return Recognition(
            transcription=self.transcription,
            notes=self.notes,
            dates=dates,
            engine="mock",
            model=model or "mock",
            raw=self.transcription,
        )


class OllamaBackend:
    """POST /api/generate with images and a JSON schema, as Ollama documents."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        timeout: int = DEFAULT_TIMEOUT,
        poster=None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = min(max(timeout, 1), MAX_TIMEOUT)
        self._poster = poster or post_json

    def recognize(self, image: Path, *, model: str) -> Recognition:
        name = model.strip()
        if not name:
            raise BackendError(
                "No model name. Type a local Ollama vision model, or type the back by hand."
            )
        if not valid_host(self.host):
            raise BackendError("The Ollama host URL is not valid.")
        encoded = encode_image(image)
        payload = {
            "model": name,
            "prompt": PROMPT,
            "images": [encoded],
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": 0},
        }
        url = self.host + "/api/generate"
        try:
            body = self._poster(url, payload, timeout=self.timeout)
        except TimeoutError as exc:
            raise BackendError("Ollama timed out.") from exc
        except urllib.error.HTTPError as exc:
            raise BackendError(_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise BackendError(
                "Ollama is not reachable. Start it yourself if you want a reading."
            ) from exc
        except OSError as exc:
            raise BackendError("Ollama is not reachable.") from exc
        return parse_ollama_body(body, model=name)


def encode_image(path: Path) -> str:
    """JPEG base64, shrunk so the POST stays bounded. Paths in the file are unused."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise BackendError("The back image could not be read.") from exc
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            converted = image.convert("RGB")
            converted.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buffer = BytesIO()
            converted.save(buffer, format="JPEG", quality=85)
    except Exception as exc:
        raise BackendError("The back image could not be opened.") from exc
    blob = buffer.getvalue()
    encoded = base64.b64encode(blob).decode("ascii")
    if len(encoded) > MAX_ENCODED:
        raise BackendError("The back image is too large to send.")
    return encoded


def post_json(url: str, payload: dict, *, timeout: int) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE + 1)


def parse_ollama_body(body: bytes, *, model: str) -> Recognition:
    if len(body) > MAX_RESPONSE:
        raise BackendError("The Ollama reply was too large.")
    try:
        outer = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise BackendError("The Ollama reply was not JSON.") from exc
    if not isinstance(outer, dict):
        raise BackendError("The Ollama reply was not an object.")
    if outer.get("error"):
        message = outer.get("error")
        if not isinstance(message, str) or not message.strip():
            message = "Ollama returned an error."
        raise BackendError(message.strip()[:200])
    raw = outer.get("response")
    if not isinstance(raw, str) or not raw.strip():
        raise BackendError("The Ollama reply had no text.")
    try:
        inner = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackendError("The Ollama reply was not structured JSON.") from exc
    return parse_structured(inner, engine="ollama", model=model, raw=raw)


def parse_structured(inner: object, *, engine: str, model: str, raw: str) -> Recognition:
    if not isinstance(inner, dict):
        raise BackendError("The model JSON was not an object.")
    transcription = inner.get("transcription")
    if not isinstance(transcription, str):
        raise BackendError("The model JSON was missing transcription.")
    notes = inner.get("notes")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise BackendError("The model JSON notes field was not text.")
    dates = parse_model_dates(inner.get("dates"))
    return Recognition(
        transcription=_clean_text(transcription),
        notes=_clean_text(notes),
        dates=dates,
        engine=engine,
        model=model,
        raw=_clean_text(raw),
    )


def _clean_text(text: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    return cleaned[:MAX_TEXT]


def _http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code == 404:
        return "That Ollama model was not found."
    if exc.code == 400:
        return "Ollama rejected the request."
    return f"Ollama HTTP {exc.code}."
