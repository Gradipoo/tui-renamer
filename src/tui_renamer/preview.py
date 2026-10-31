"""Thumbnail and text peek for the highlighted file."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

THUMB_EDGE = 160
CACHE_LIMIT = 32
DEBOUNCE_SECONDS = 0.08
PANE_MIN_WIDTH = 24
PANE_MIN_HEIGHT = 12
TEXT_BYTES = 2048
TEXT_LINES = 8

RASTER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"})
HEIF_SUFFIXES = frozenset({".heic", ".heif"})
TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".log",
        ".py",
        ".toml",
        ".yml",
        ".yaml",
        ".xml",
        ".html",
        ".css",
        ".ini",
        ".cfg",
        ".rst",
    }
)

_heif_ready: bool | None = None


@dataclass
class Preview:
    """What the detail pane can show for one file."""

    caption: str
    body: str = ""
    image: PILImage.Image | None = None
    show_image: bool = False


class ThumbCache:
    """Small LRU keyed by path, size, and mtime."""

    def __init__(self, limit: int = CACHE_LIMIT) -> None:
        self.limit = limit
        self._items: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: tuple[str, int, int]) -> Any | None:
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def put(self, key: tuple[str, int, int], value: Any) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)


def format_size(size: int) -> str:
    """Short byte size for a caption."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def pane_too_small(width: int, height: int) -> bool:
    """Hide the picture when the detail pane has not enough room."""
    if width <= 0 or height <= 0:
        return False
    return width < PANE_MIN_WIDTH or height < PANE_MIN_HEIGHT


def heif_ready() -> bool:
    """True when pillow-heif can register an opener."""
    global _heif_ready
    if _heif_ready is not None:
        return _heif_ready
    try:
        from pillow_heif import register_heif_opener
    except Exception:
        _heif_ready = False
        return False
    try:
        register_heif_opener()
    except Exception:
        _heif_ready = False
        return False
    _heif_ready = True
    return True


def peek_text(
    path: Path, *, max_bytes: int = TEXT_BYTES, max_lines: int = TEXT_LINES
) -> str | None:
    """First lines of a text file. None when the file looks binary."""
    try:
        blob = path.read_bytes()[:max_bytes]
    except OSError:
        return None
    if b"\x00" in blob:
        return None
    text = blob.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[:max_lines])


def cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_size, getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))


def build_preview(path: Path, cache: ThumbCache) -> Preview:
    """Caption, text, or thumbnail for one path. Does not touch the UI."""
    suffix = path.suffix.lower()
    try:
        size_text = format_size(path.stat().st_size)
    except OSError:
        return Preview(caption=f"{path.name}  unreadable")
    if suffix in HEIF_SUFFIXES and not heif_ready():
        return Preview(caption=f"{path.name}  HEIC needs the heif extra")
    if suffix in RASTER_SUFFIXES or suffix in HEIF_SUFFIXES:
        thumb = _cached_thumb(path, cache)
        if thumb is None:
            label = suffix if suffix else "file"
            return Preview(caption=f"{path.name}  {label}  {size_text}")
        image, width, height = thumb
        return Preview(
            caption=f"{path.name}  {width}x{height}  {size_text}",
            image=image,
            show_image=True,
        )
    if suffix in TEXT_SUFFIXES:
        text = peek_text(path)
        if text is not None:
            return Preview(caption=f"{path.name}  {size_text}", body=text)
    label = suffix if suffix else "file"
    return Preview(caption=f"{path.name}  {label}  {size_text}")


def plain_preview(path: Path) -> Preview:
    """Caption only, used when decoding fails."""
    suffix = path.suffix.lower() or "file"
    try:
        size_text = format_size(path.stat().st_size)
    except OSError:
        return Preview(caption=f"{path.name}  unreadable")
    return Preview(caption=f"{path.name}  {suffix}  {size_text}")


def load_image_widget() -> type | None:
    """Import the textual-image widget. None when it cannot be used."""
    try:
        from textual_image.widget import Image
    except Exception:
        return None
    return Image


def warm_image_widget() -> None:
    """Probe the terminal before Textual takes stdin. Skipped under pytest."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    load_image_widget()


def _cached_thumb(path: Path, cache: ThumbCache) -> tuple[PILImage.Image, int, int] | None:
    key = cache_key(path)
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    try:
        with PILImage.open(path) as opened:
            width, height = opened.size
            frame = opened.convert("RGBA")
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    frame.thumbnail((THUMB_EDGE, THUMB_EDGE))
    packed = (frame, width, height)
    if key is not None:
        cache.put(key, packed)
    return packed
