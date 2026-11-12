"""Thumbnail and text peek for the highlighted file."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from tui_renamer.cr3 import embedded_preview_jpeg
from tui_renamer.extras import offer_for

THUMB_EDGE = 160
CACHE_LIMIT = 32
DEBOUNCE_SECONDS = 0.08
PANE_MIN_WIDTH = 24
PANE_MIN_HEIGHT = 8
TEXT_BYTES = 2048
TEXT_LINES = 8

RASTER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"})
HEIF_SUFFIXES = frozenset({".heic", ".heif"})
JXL_SUFFIXES = frozenset({".jxl"})
VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v"})
CR3_SUFFIXES = frozenset({".cr3"})
FFMPEG_TIMEOUT = 8
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
_jxl_ready: bool | None = None


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
    """Hide the picture when the detail pane cannot hold an 8-row thumbnail.

    A 31-row terminal leaves this pane about 10 rows, so the minimum is the
    thumbnail height rather than the preview slot's 12-row cap.
    """
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


def forget_optional_readers() -> None:
    """Check HEIC and JPEG XL again after a package install."""
    global _heif_ready, _jxl_ready
    _heif_ready = None
    _jxl_ready = None


def jxl_ready() -> bool:
    """True when pillow-jxl-plugin has registered an opener."""
    global _jxl_ready
    if _jxl_ready is not None:
        return _jxl_ready
    try:
        import pillow_jxl  # noqa: F401
    except Exception:
        _jxl_ready = False
        return False
    _jxl_ready = True
    return True


def find_ffmpeg() -> str | None:
    """ffmpeg on PATH, used for one video frame. None when it is missing."""
    return shutil.which("ffmpeg")


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
        return Preview(caption=_optional_caption(path.name, "HEIC", "heif"))
    if suffix in JXL_SUFFIXES and not jxl_ready():
        return Preview(caption=_optional_caption(path.name, "JPEG XL", "jxl"))
    if suffix in VIDEO_SUFFIXES and find_ffmpeg() is None:
        return Preview(caption=f"{path.name}  video needs ffmpeg")
    if suffix in RASTER_SUFFIXES or suffix in HEIF_SUFFIXES or suffix in JXL_SUFFIXES:
        thumb = _cached_thumb(path, cache)
    elif suffix in VIDEO_SUFFIXES:
        thumb = _cached_from_bytes(path, cache, _ffmpeg_frame(path))
    elif suffix in CR3_SUFFIXES:
        thumb = _cached_from_bytes(path, cache, embedded_preview_jpeg(path))
    else:
        thumb = None
    if thumb is not None:
        image, width, height = thumb
        return Preview(
            caption=f"{path.name}  {width}x{height}  {size_text}",
            image=image,
            show_image=True,
        )
    if suffix in RASTER_SUFFIXES or suffix in HEIF_SUFFIXES or suffix in JXL_SUFFIXES:
        label = suffix if suffix else "file"
        return Preview(caption=f"{path.name}  {label}  {size_text}")
    if suffix in VIDEO_SUFFIXES or suffix in CR3_SUFFIXES:
        label = suffix if suffix else "file"
        return Preview(caption=f"{path.name}  {label}  {size_text}")
    if suffix in TEXT_SUFFIXES:
        text = peek_text(path)
        if text is not None:
            return Preview(caption=f"{path.name}  {size_text}", body=text)
    label = suffix if suffix else "file"
    return Preview(caption=f"{path.name}  {label}  {size_text}")


def _optional_caption(filename: str, label: str, extra: str) -> str:
    """Say which package manager can install a missing plugin."""
    offer = offer_for(extra)
    if offer is None:
        return f"{filename}  {label} needs the {extra} package, which this system does not ship"
    return f"{filename}  {label} needs the {extra} package. Install with {offer.manager}."


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
            packed = _frame_thumb(opened)
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    if key is not None:
        cache.put(key, packed)
    return packed


def _cached_from_bytes(
    path: Path, cache: ThumbCache, blob: bytes | None
) -> tuple[PILImage.Image, int, int] | None:
    if not blob:
        return None
    key = cache_key(path)
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    try:
        with PILImage.open(BytesIO(blob)) as opened:
            packed = _frame_thumb(opened)
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    if key is not None:
        cache.put(key, packed)
    return packed


def _frame_thumb(opened: PILImage.Image) -> tuple[PILImage.Image, int, int]:
    """First frame, then a thumbnail. GIF and other sequences stay on frame 0."""
    try:
        opened.seek(0)
    except EOFError:
        pass
    width, height = opened.size
    frame = opened.convert("RGBA")
    frame.thumbnail((THUMB_EDGE, THUMB_EDGE))
    return frame, width, height


def _ffmpeg_frame(path: Path) -> bytes | None:
    """One PNG frame from ffmpeg. None when the tool is missing or the file fails."""
    tool = find_ffmpeg()
    if not tool:
        return None
    try:
        result = subprocess.run(
            [
                tool,
                "-v",
                "error",
                "-ss",
                "0",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout
