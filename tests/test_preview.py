"""Thumbnails, text peek, and optional picture formats."""

from __future__ import annotations

import subprocess
from io import BytesIO
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
    assert pane_too_small(24, 7) is True
    assert pane_too_small(24, 8) is False
    assert pane_too_small(24, 10) is False
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


def test_gif_preview_uses_the_first_frame(tmp_path: Path) -> None:
    path = tmp_path / "anim.gif"
    red = Image.new("RGB", (20, 10), (255, 0, 0))
    blue = Image.new("RGB", (20, 10), (0, 0, 255))
    red.save(path, save_all=True, append_images=[blue], duration=100, loop=0, format="GIF")
    shown = build_preview(path, ThumbCache())
    assert shown.show_image is True
    assert shown.image is not None
    assert shown.image.getpixel((0, 0))[:3] == (255, 0, 0)


def test_jxl_without_the_extra(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.preview.jxl_ready", lambda: False)
    path = tmp_path / "photo.jxl"
    path.write_bytes(b"not a jxl")
    shown = build_preview(path, ThumbCache())
    assert "jxl" in shown.caption
    assert shown.show_image is False


def test_video_without_ffmpeg_names_the_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.preview.find_ffmpeg", lambda: None)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a video")
    shown = build_preview(path, ThumbCache())
    assert "ffmpeg" in shown.caption
    assert shown.show_image is False


def test_video_preview_uses_one_ffmpeg_frame(tmp_path: Path, monkeypatch) -> None:
    frame = BytesIO()
    Image.new("RGB", (30, 10), (0, 180, 0)).save(frame, format="PNG")
    seen: dict[str, list[str]] = {}

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen["args"] = list(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=frame.getvalue())

    monkeypatch.setattr("tui_renamer.preview.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("tui_renamer.preview.subprocess.run", fake_run)
    path = tmp_path / "clip.mov"
    path.write_bytes(b"not a video")
    shown = build_preview(path, ThumbCache())
    assert shown.show_image is True
    assert shown.image is not None
    assert shown.image.getpixel((0, 0))[:3] == (0, 180, 0)
    assert seen["args"][0] == "ffmpeg"
    assert "pipe:1" in seen["args"]
    assert "-frames:v" in seen["args"]


def test_cr3_preview_uses_the_embedded_jpeg(tmp_path: Path) -> None:
    jpeg = BytesIO()
    Image.new("RGB", (16, 8), (10, 20, 30)).save(jpeg, format="JPEG")
    path = tmp_path / "photo.cr3"
    path.write_bytes(_prvw_tag(b"\x00\x00\x00\x00") + _prvw_tag(jpeg.getvalue()))
    shown = build_preview(path, ThumbCache())
    assert shown.show_image is True
    assert shown.image is not None
    pixel = shown.image.getpixel((0, 0))
    expected = (10, 20, 30)
    assert all(abs(pixel[channel] - expected[channel]) <= 12 for channel in range(3))


def test_cr3_without_a_jpeg_preview_stays_caption_only(tmp_path: Path) -> None:
    path = tmp_path / "photo.cr3"
    path.write_bytes(_prvw_tag(b"\x00\x00\x00\x00"))
    shown = build_preview(path, ThumbCache())
    assert shown.show_image is False
    assert ".cr3" in shown.caption


def test_other_raw_is_not_previewed(tmp_path: Path) -> None:
    path = tmp_path / "photo.cr2"
    path.write_bytes(b"not a preview")
    shown = build_preview(path, ThumbCache())
    assert shown.show_image is False
    assert ".cr2" in shown.caption


def _prvw_tag(payload: bytes) -> bytes:
    return (
        (24 + len(payload)).to_bytes(4, "big")
        + b"PRVW"
        + (b"\x00" * 12)
        + len(payload).to_bytes(4, "big")
        + payload
    )
