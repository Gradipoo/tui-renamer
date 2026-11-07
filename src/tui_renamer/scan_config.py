"""Local scan-review backend settings. The last folder is not stored."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from tui_renamer import view_config

_FILE_NAME = "scan.json"
DEFAULT_HOST = "http://127.0.0.1:11434"
MAX_HOST = 200
MAX_MODEL = 80


@dataclass(frozen=True)
class ScanSettings:
    host: str = DEFAULT_HOST
    model: str = ""


def load_scan_settings() -> ScanSettings:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ScanSettings()
    if not isinstance(raw, dict):
        return ScanSettings()
    host = raw.get("host", DEFAULT_HOST)
    model = raw.get("model", "")
    if not isinstance(host, str) or not valid_host(host):
        host = DEFAULT_HOST
    if not isinstance(model, str):
        model = ""
    return ScanSettings(host=host.strip()[:MAX_HOST], model=model.strip()[:MAX_MODEL])


def save_scan_settings(settings: ScanSettings) -> None:
    try:
        folder = view_config.config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"host": settings.host, "model": settings.model}
        _path().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def valid_host(host: str) -> bool:
    """http(s) URL with a host. file: and javascript: are rejected."""
    if not host or len(host) > MAX_HOST:
        return False
    parsed = urlparse(host)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def _path() -> Path:
    return view_config.config_dir() / _FILE_NAME
