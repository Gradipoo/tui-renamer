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
    "Read this photograph. Extract visible writing and dates. Give short scene "
    "tokens. Say whether people are present and a count. Do not identify who "
    "anyone is. Do not guess names or sensitive attributes. Do not invent a "
    "month or day when writing only has a year. Dates copied from writing have "
    "origin written. A visual era guess has origin inferred and uncertain true. "
    "Return JSON only."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "transcription": {"type": "string"},
        "notes": {"type": "string"},
        "tokens": {"type": "array", "items": {"type": "string"}},
        "people_present": {"type": ["boolean", "null"]},
        "people_count": {"type": ["integer", "null"]},
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
                    "origin": {"type": "string", "enum": ["written", "inferred"]},
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
    tokens: list[str] = field(default_factory=list)
    people_present: bool | None = None
    people_count: int | None = None


class ScanBackend(Protocol):
    def recognize(self, image: Path, *, model: str) -> Recognition:
        """Read a back image. Raise BackendError on failure."""

    def list_vision_models(self) -> list[str]:
        """Installed vision model names, or empty."""

    def list_loaded_models(self) -> list[str]:
        """Names currently loaded in memory, or empty."""


@dataclass
class MockBackend:
    """Deterministic backend for tests. Never opens the image as a model."""

    transcription: str = "June 1952 Aunt May"
    notes: str = ""
    dates: list[ScanDate] | None = None
    error: str | None = None
    vision_models: list[str] = field(default_factory=list)
    loaded_models: list[str] = field(default_factory=list)
    discover_error: str | None = None
    tokens: list[str] = field(default_factory=lambda: ["picnic"])
    people_present: bool | None = True
    people_count: int | None = 1
    recognize_calls: list[tuple[str, str]] = field(default_factory=list)

    def recognize(self, image: Path, *, model: str) -> Recognition:
        self.recognize_calls.append((str(image), model))
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
            tokens=list(self.tokens),
            people_present=self.people_present,
            people_count=self.people_count,
        )

    def list_vision_models(self) -> list[str]:
        if self.discover_error:
            raise BackendError(self.discover_error)
        return list(self.vision_models)

    def list_loaded_models(self) -> list[str]:
        return list(self.loaded_models)


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
                "No model name. Type a local Ollama vision model, or type notes by hand."
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

    def list_loaded_models(self) -> list[str]:
        if not valid_host(self.host) or not host_is_local(self.host):
            return []
        try:
            body = self._getter(self.host + "/api/ps", timeout=DISCOVER_TIMEOUT)
        except (TimeoutError, urllib.error.URLError, OSError, urllib.error.HTTPError):
            return []
        try:
            return parse_tags(body)
        except BackendError:
            return []

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
        raise BackendError("The image could not be read.") from exc
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            converted = image.convert("RGB")
            converted.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buffer = BytesIO()
            converted.save(buffer, format="JPEG", quality=85)
    except Exception as exc:
        raise BackendError("The image could not be opened.") from exc
    blob = buffer.getvalue()
    encoded = base64.b64encode(blob).decode("ascii")
    if len(encoded) > MAX_ENCODED:
        raise BackendError("The image is too large to send.")
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
    tokens = _string_list(inner.get("tokens"))
    present = inner.get("people_present")
    if not isinstance(present, bool):
        present = None
    count = inner.get("people_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > 99:
        count = None
    return Recognition(
        transcription=_clean_text(transcription),
        notes=_clean_text(notes),
        dates=dates,
        engine=engine,
        model=model,
        raw=_clean_text(raw),
        tokens=tokens,
        people_present=present,
        people_count=count,
    )


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for item in raw[:12]:
        if isinstance(item, str):
            clean = _clean_text(item)[:80]
            if clean:
                found.append(clean)
    return found


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
