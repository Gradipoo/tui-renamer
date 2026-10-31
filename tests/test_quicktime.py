"""Hand-built QuickTime atoms. exiftool is not invoked."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tui_renamer.core import RenameRules, neighbor_flags
from tui_renamer.metadata import inspect_file
from tui_renamer.quicktime import parse_creationdate, read_quicktime

UTC = timezone.utc
EPOCH = datetime(1904, 1, 1, tzinfo=UTC)
CAPTURE = datetime(2020, 6, 1, 17, 0, tzinfo=UTC)


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def fullbox(kind: bytes, payload: bytes) -> bytes:
    return box(kind, b"\x00\x00\x00\x00" + payload)


def mvhd(moment: datetime, *, version: int = 0) -> bytes:
    seconds = int((moment.astimezone(UTC) - EPOCH).total_seconds())
    if version == 1:
        payload = bytes([1, 0, 0, 0]) + struct.pack(">QQ", seconds, seconds)
        payload += struct.pack(">IQ", 1000, 0)
    else:
        payload = struct.pack(">III", 0, seconds, seconds)
        payload += struct.pack(">II", 1000, 0)
    return box(b"mvhd", payload + b"\x00" * 16)


def creation_meta(text: str) -> bytes:
    name = b"com.apple.quicktime.creationdate"
    entry = struct.pack(">I", 8 + len(name)) + b"mdta" + name
    keys = fullbox(b"keys", struct.pack(">I", 1) + entry)
    data = box(b"data", struct.pack(">II", 1, 0) + text.encode("ascii"))
    item = box((1).to_bytes(4, "big"), data)
    return fullbox(b"meta", keys + box(b"ilst", item))


def movie(*parts: bytes) -> bytes:
    return box(b"ftyp", b"qt  \x00\x00\x00\x00") + box(b"moov", b"".join(parts))


def test_mvhd_is_utc_from_the_1904_epoch() -> None:
    clock = read_quicktime_bytes(movie(mvhd(CAPTURE)))
    assert clock.naive == datetime(2020, 6, 1, 17, 0)
    assert clock.offset == UTC
    assert clock.label == "QuickTime mvhd"
    assert clock.offset_text == "+00:00"


def test_mvhd_version_1_and_a_leading_free_box() -> None:
    blob = box(b"free", b"\x00" * 40) + movie(mvhd(CAPTURE, version=1))
    clock = read_quicktime_bytes(blob)
    assert clock.naive == datetime(2020, 6, 1, 17, 0)
    assert clock.label == "QuickTime mvhd"


def test_creationdate_beats_mvhd_when_it_has_a_timezone() -> None:
    meta = box(b"udta", creation_meta("2020-06-01T10:00:00-07:00"))
    clock = read_quicktime_bytes(movie(mvhd(datetime(2010, 1, 1, tzinfo=UTC)), meta))
    assert clock.naive == datetime(2020, 6, 1, 10, 0)
    assert clock.offset == timezone(timedelta(hours=-7))
    assert clock.label == "QuickTime creation date"
    assert clock.offset_text == "-07:00"


def test_creationdate_accepts_z_and_compact_offsets() -> None:
    zulu = parse_creationdate("2020-06-01T10:00:00.5Z")
    assert zulu is not None
    assert zulu.offset == UTC
    assert zulu.offset_text == "+00:00"
    compact = parse_creationdate("2020-06-01T10:00:00-0700")
    assert compact is not None
    assert compact.offset == timezone(timedelta(hours=-7))
    assert parse_creationdate("2020-06-01T10:00:00") is None


def test_creationdate_without_a_timezone_falls_back_to_mvhd() -> None:
    meta = box(b"udta", creation_meta("2020-06-01T10:00:00"))
    clock = read_quicktime_bytes(movie(mvhd(CAPTURE), meta))
    assert clock.label == "QuickTime mvhd"
    assert clock.naive == datetime(2020, 6, 1, 17, 0)


def test_empty_and_truncated_files_have_no_clock(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert read_quicktime(empty).naive is None
    broken = tmp_path / "short.mov"
    broken.write_bytes(b"\x00\x00\x00\x20moov")
    assert read_quicktime(broken).naive is None


def test_capture_beats_mtime_and_does_not_count_as_exif(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.subprocess.run", _forbid_exiftool)
    path = tmp_path / "clip.mp4"
    path.write_bytes(movie(mvhd(CAPTURE)))
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source == "Q"
    assert facts.winner_instant == CAPTURE
    assert facts.has_exif is False
    assert any(item.label.startswith("QuickTime mvhd") for item in facts.candidates)


def test_json_beats_quicktime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.subprocess.run", _forbid_exiftool)
    path = tmp_path / "clip.mp4"
    path.write_bytes(movie(mvhd(CAPTURE)))
    sidecar = path.with_suffix(path.suffix + ".json")
    sidecar.write_text(
        json.dumps({"photoTakenTime": {"timestamp": "1600000000"}}),
        encoding="utf-8",
    )
    facts = inspect_file(path, RenameRules(use_json_data=True), UTC)
    assert facts.winner_source == "J"
    assert facts.winner_instant == datetime.fromtimestamp(1600000000, tz=UTC)


def test_old_capture_is_the_year_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.find_exiftool", lambda: None)
    path = tmp_path / "old.mp4"
    path.write_bytes(movie(mvhd(datetime(1985, 6, 1, tzinfo=UTC))))
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source == "Q"
    assert neighbor_flags([facts.winner_instant], datetime(2026, 1, 1, tzinfo=UTC)) == ["Y"]


def test_video_without_a_date_is_not_exif(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.find_exiftool", lambda: None)
    path = tmp_path / "plain.m4v"
    path.write_bytes(b"not a movie")
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source != "E"
    assert facts.winner_source != "Q"
    assert facts.has_exif is False


def test_exiftool_creationdate_is_the_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.find_exiftool", lambda: "exiftool")
    monkeypatch.setattr("tui_renamer.metadata.subprocess.run", _exiftool_creation)
    path = tmp_path / "clip.mov"
    path.write_bytes(b"no atoms here!!")
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source == "Q"
    assert facts.winner_instant == datetime(2020, 6, 1, 17, 0, tzinfo=UTC)
    assert facts.has_exif is False


def test_exiftool_timeout_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.find_exiftool", lambda: "exiftool")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="exiftool", timeout=8)

    monkeypatch.setattr("tui_renamer.metadata.subprocess.run", explode)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"no atoms here!!")
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source != "Q"
    assert facts.has_exif is False


def test_heic_without_the_extra_keeps_the_file_time(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.metadata.heif_ready", lambda: False)
    path = tmp_path / "photo.heic"
    path.write_bytes(b"not a heic")
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source != "E"
    assert facts.has_exif is False


def read_quicktime_bytes(blob: bytes):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "clip.mp4"
        path.write_bytes(blob)
        return read_quicktime(path)


def _forbid_exiftool(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("exiftool should not run when the atoms have a date")


def _exiftool_creation(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    payload = [
        {
            "CreationDate": "2020:06:01 10:00:00-07:00",
            "CreateDate": "1999:01:01 00:00:00+00:00",
        }
    ]
    return subprocess.CompletedProcess(args=["exiftool"], returncode=0, stdout=json.dumps(payload))
