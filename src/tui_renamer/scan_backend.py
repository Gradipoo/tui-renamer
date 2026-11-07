"""Pluggable scan-back recognition.

The first backend talks to a local Ollama HTTP server that the user already
runs. Nothing is installed or started from this app. Image bytes are posted
only to the configured host. Model JSON is untrusted and strictly parsed.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image

from tui_renamer.scan_config import DEFAULT_HOST, host_is_local, valid_host
from tui_renamer.scan_dates import ScanDate, parse_model_dates
from tui_renamer.scan_models import DEFAULT, MAX_PULL, pull_command
from tui_renamer.scan_notes import MAX_TEXT

MAX_IMAGE_EDGE = 1280
MAX_ENCODED = 4 * 1024 * 1024
MAX_RESPONSE = 1 * 1024 * 1024
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120
DISCOVER_TIMEOUT = 5
DISCOVER_BUDGET = 12
MAX_DISCOVER = 24

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

    def list_vision_models(self) -> list[str]:
        """Installed vision model names, or empty."""


@dataclass
class MockBackend:
    """Deterministic backend for tests. Never opens the image as a model."""

    transcription: str = "June 1952 Aunt May"
    notes: str = ""
    dates: list[ScanDate] | None = None
    error: str | None = None
    vision_models: list[str] = field(default_factory=list)
    discover_error: str | None = None

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

    def list_vision_models(self) -> list[str]:
        if self.discover_error:
            raise BackendError(self.discover_error)
        return list(self.vision_models)


class OllamaBackend:
    """POST /api/generate with images and a JSON schema, as Ollama documents."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        timeout: int = DEFAULT_TIMEOUT,
        poster=None,
        getter=None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = min(max(timeout, 1), MAX_TIMEOUT)
        self._poster = poster or post_json
        self._getter = getter or get_json

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
            raise BackendError(_http_error(exc, model=name)) from exc
        except urllib.error.URLError as exc:
            raise BackendError(
                "Ollama is not reachable. Start it yourself if you want a reading."
            ) from exc
        except OSError as exc:
            raise BackendError("Ollama is not reachable.") from exc
        return parse_ollama_body(body, model=name)

    def list_vision_models(self) -> list[str]:
        """Installed vision models on loopback only. Never scans a remote host."""
        if not valid_host(self.host):
            raise BackendError("The Ollama host URL is not valid.")
        if not host_is_local(self.host):
            return []
        names = self._list_names()
        found: list[str] = []
        deadline = time.monotonic() + DISCOVER_BUDGET
        for name in names[:MAX_DISCOVER]:
            if time.monotonic() >= deadline:
                break
            if self._model_has_vision(name):
                found.append(name)
        return found

    def _list_names(self) -> list[str]:
        url = self.host + "/api/tags"
        try:
            body = self._getter(url, timeout=DISCOVER_TIMEOUT)
        except TimeoutError as exc:
            raise BackendError("Listing local Ollama models timed out.") from exc
        except urllib.error.HTTPError as exc:
            raise BackendError(_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise BackendError(
                "Ollama is not reachable. Start it yourself with: ollama serve"
            ) from exc
        except OSError as exc:
            raise BackendError(
                "Ollama is not reachable. Start it yourself with: ollama serve"
            ) from exc
        return parse_tags(body)

    def _model_has_vision(self, name: str) -> bool:
        url = self.host + "/api/show"
        try:
            body = self._poster(url, {"model": name}, timeout=DISCOVER_TIMEOUT)
        except (TimeoutError, urllib.error.URLError, OSError, BackendError):
            return False
        try:
            return show_has_vision(body)
        except BackendError:
            return False


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


def get_json(url: str, *, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE + 1)


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
        raise BackendError(_vision_error(message.strip()[:200], model=model))
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


def parse_tags(body: bytes) -> list[str]:
    if len(body) > MAX_RESPONSE:
        raise BackendError("The Ollama model list was too large.")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise BackendError("The Ollama model list was not JSON.") from exc
    if not isinstance(data, dict):
        raise BackendError("The Ollama model list was not an object.")
    models = data.get("models")
    if not isinstance(models, list):
        raise BackendError("The Ollama model list was missing models.")
    names: list[str] = []
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        clean = name.strip()[:MAX_PULL]
        if clean in seen:
            continue
        seen.add(clean)
        names.append(clean)
    return names


def show_has_vision(body: bytes) -> bool:
    """True when /api/show reports the documented vision capability."""
    if len(body) > MAX_RESPONSE:
        raise BackendError("The Ollama show reply was too large.")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise BackendError("The Ollama show reply was not JSON.") from exc
    if not isinstance(data, dict):
        raise BackendError("The Ollama show reply was not an object.")
    caps = data.get("capabilities")
    if isinstance(caps, list) and any(
        isinstance(item, str) and item.casefold() == "vision" for item in caps
    ):
        return True
    info = data.get("model_info")
    if isinstance(info, dict) and any(isinstance(key, str) and ".vision." in key for key in info):
        return True
    return False


def _vision_error(message: str, *, model: str) -> str:
    lower = message.casefold()
    if "vision" in lower or "image" in lower:
        return (
            f"{message} This looks like a text-only model. Pull a vision tag such as {DEFAULT.tag}."
        )
    return message


def _http_error(exc: urllib.error.HTTPError, model: str = "") -> str:
    if exc.code == 404:
        tag = model.strip() or DEFAULT.tag
        return f"That Ollama model was not found. Pull it yourself with: {pull_command(tag)}"
    if exc.code == 400:
        return "Ollama rejected the request."
    return f"Ollama HTTP {exc.code}."
