"""Remember preview and zoom. Rename rules and the last folder are not stored."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ZOOM_NAMES = frozenset({"files", "detail", "options", "preview"})
_FILE_NAME = "view.json"


@dataclass(frozen=True)
class ViewState:
    preview: bool = True
    zoom: str | None = None


def config_dir() -> Path:
    """Platform config directory for rn."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "rn"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "rn"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "rn"


def load_view() -> ViewState:
    """Saved preview and zoom. A missing or broken file uses the defaults."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ViewState()
    if not isinstance(raw, dict):
        return ViewState()
    preview = raw.get("preview", True)
    zoom = raw.get("zoom")
    if not isinstance(preview, bool):
        preview = True
    if zoom not in ZOOM_NAMES:
        zoom = None
    return ViewState(preview=preview, zoom=zoom)


def save_view(state: ViewState) -> None:
    """Write the view. A directory that cannot be created is ignored."""
    try:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"preview": state.preview, "zoom": state.zoom}
        _path().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def clear_view() -> None:
    """Delete the saved view. A missing file is fine."""
    try:
        _path().unlink(missing_ok=True)
    except OSError:
        return


def _path() -> Path:
    return config_dir() / _FILE_NAME
