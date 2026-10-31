"""Thumbnails, text peek, and the HEIC extra."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from tui_renamer.preview import (
    ThumbCache,
    build_preview,
    format_size,
    pane_too_small,
    peek_text,
)


def test_format_size_and_pane_limit() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1024 * 1024) == "1.0 MB"
    assert pane_too_small(23, 20) is True
    assert pane_too_small(24, 11) is True
    assert pane_too_small(24, 12) is False
    assert pane_too_small(0, 0) is False


def test_thumbnail_long_side_and_cache(tmp_path: Path) -> None:
    path = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), (1, 2, 3)).save(path)
    cache = ThumbCache()
    shown = build_preview(path, cache)
    assert shown.image is not None
    assert shown.show_image is True
    assert shown.image.size == (160, 40)
    assert "400x100" in shown.caption
    again = build_preview(path, cache)
    assert again.image is shown.image
    assert len(cache) == 1


def test_cache_drops_the_oldest() -> None:
    cache = ThumbCache(limit=32)
    for index in range(33):
        cache.put((str(index), index, index), index)
    assert len(cache) == 32
    assert cache.get(("0", 0, 0)) is None
    assert cache.get(("32", 32, 32)) == 32


def test_text_peek_stops_at_eight_lines_and_nul(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("\n".join(f"line {index}" for index in range(10)), encoding="utf-8")
    text = peek_text(notes)
    assert text is not None
    assert text.splitlines() == [f"line {index}" for index in range(8)]
    binary = tmp_path / "notes.txt"
    binary.write_bytes(b"hello\x00world\nsecond\n")
    assert peek_text(binary) is None
    shown = build_preview(binary, ThumbCache())
    assert shown.body == ""
    assert shown.show_image is False
    assert ".txt" in shown.caption


def test_heic_without_the_extra(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.preview.heif_ready", lambda: False)
    path = tmp_path / "photo.heic"
    path.write_bytes(b"not a photo")
    shown = build_preview(path, ThumbCache())
    assert "heif" in shown.caption
    assert shown.show_image is False
