"""EXIF read and write tests. JPEGs are synthetic. exiftool is not required."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import piexif
import pytest
from PIL import Image

from tui_renamer.core import RenameRules
from tui_renamer.metadata import (
    DATETIME,
    OFFSET_TIME,
    OFFSET_TIME_DIGITIZED,
    OFFSET_TIME_ORIGINAL,
    WriteCounts,
    degrees_to_dms_rational,
    generic_exiftool_command,
    inspect_file,
    parse_offset,
    read_exif,
    set_birthtime,
    set_filesystem_times,
    setfile_command,
    snapshot_mode,
    tally_write,
    timezone_write_message,
    video_exiftool_command,
    windows_filetime,
    write_file_metadata_and_offset,
    write_geodata,
    write_image_exif_datetime,
)
from tui_renamer.preview import HEIF_SUFFIXES, heif_ready

UTC = timezone.utc
KOLKATA = timezone(timedelta(hours=5, minutes=30))


def _jpeg(path: Path) -> None:
    Image.new("RGB", (8, 8), (180, 20, 20)).save(path, format="JPEG")


def _stamp_tags(path: Path, offsets: dict[int, str], when: str = "2020:06:01 15:00:00") -> None:
    exif = {"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: when}, "GPS": {}, "1st": {}}
    exif["Exif"].update(offsets)
    piexif.insert(piexif.dump(exif), str(path))


def test_parse_offset() -> None:
    assert parse_offset("+05:30") == KOLKATA
    assert parse_offset("-08:00") == timezone(timedelta(hours=-8))
    assert parse_offset("+00:00") == UTC
    assert parse_offset("0500") is None
    assert parse_offset("nope") is None
    assert parse_offset("") is None


def test_read_prefers_offset_time_original_from_the_exif_ifd(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    _stamp_tags(
        path,
        {
            OFFSET_TIME: "+01:00",
            OFFSET_TIME_ORIGINAL: "+05:30",
            OFFSET_TIME_DIGITIZED: "-04:00",
        },
    )
    clock = read_exif(path)
    assert clock.naive == datetime(2020, 6, 1, 15, 0, 0)
    assert clock.offset == KOLKATA
    assert clock.offset_text == "+05:30"
    assert clock.label == "EXIF DateTimeOriginal"


def test_read_falls_back_to_offset_time_not_digitized(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    _stamp_tags(path, {OFFSET_TIME: "-08:00", OFFSET_TIME_DIGITIZED: "+09:00"})
    clock = read_exif(path)
    assert clock.offset == timezone(timedelta(hours=-8))

    only_digitized = tmp_path / "other.jpg"
    _jpeg(only_digitized)
    _stamp_tags(only_digitized, {OFFSET_TIME_DIGITIZED: "+09:00"})
    assert read_exif(only_digitized).offset is None


def test_read_uses_datetime_when_original_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    exif = {
        "0th": {piexif.ImageIFD.DateTime: "2019:01:02 03:04:05"},
        "Exif": {},
        "GPS": {},
        "1st": {},
    }
    piexif.insert(piexif.dump(exif), str(path))
    clock = read_exif(path)
    assert clock.naive == datetime(2019, 1, 2, 3, 4, 5)
    assert clock.label == "EXIF DateTime"
    assert read_exif(tmp_path / "clip.mp4").naive is None


@pytest.mark.skipif(not heif_ready(), reason="pillow-heif is not installed")
def test_heic_capture_date_uses_the_exif_letter(tmp_path: Path) -> None:
    path = tmp_path / "photo.heic"
    image = Image.new("RGB", (8, 8), (1, 2, 3))
    exif = Image.Exif()
    exif[DATETIME] = "2020:06:01 15:00:00"
    image.save(path, format="HEIF", exif=exif.tobytes())
    clock = read_exif(path, extra_suffixes=HEIF_SUFFIXES)
    assert clock.naive == datetime(2020, 6, 1, 15, 0, 0)
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.winner_source == "E"
    assert facts.has_exif is True


def test_write_round_trip_stores_all_three_offset_tags(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    moment = datetime(2024, 1, 15, 12, 30, 0, tzinfo=KOLKATA)
    assert write_image_exif_datetime(path, moment) is True
    loaded = piexif.load(str(path))
    assert loaded["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2024:01:15 12:30:00"
    for tag in (OFFSET_TIME, OFFSET_TIME_ORIGINAL, OFFSET_TIME_DIGITIZED):
        assert loaded["Exif"][tag] == b"+05:30"
    clock = read_exif(path)
    assert clock.naive == datetime(2024, 1, 15, 12, 30, 0)
    assert clock.offset == KOLKATA
    facts = inspect_file(path, RenameRules(), UTC)
    assert facts.origtime_text == "2024-01-15-070000"


def test_dump_failure_retries_with_a_clean_ifd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    real_dump = piexif.dump
    calls = {"n": 0}

    def dump(data: dict) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("bad maker note")
        return real_dump(data)

    monkeypatch.setattr("tui_renamer.metadata.piexif.dump", dump)
    moment = datetime(2021, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert write_image_exif_datetime(path, moment) is True
    assert calls["n"] == 2
    assert read_exif(path).naive == datetime(2021, 2, 3, 4, 5, 6)


def test_video_and_still_commands_are_not_run() -> None:
    moment = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone(timedelta(hours=-7)))
    video = video_exiftool_command("exiftool", Path("clip.mov"), moment)
    assert video[:2] == ["exiftool", "-overwrite_original"]
    assert "-QuickTime:CreationDate=2024:05:06 07:08:09-07:00" in video
    assert any(part.startswith("-QuickTime:CreateDate=2024:05:06 14:08:09") for part in video)
    still = generic_exiftool_command("exiftool", Path("scan.heic"), moment)
    assert "-AllDates=2024:05:06 07:08:09" in still
    assert "-OffsetTimeOriginal=-07:00" in still
    assert "-OffsetTimeDigitized=-07:00" in still


def test_write_dispatch(tmp_path: Path) -> None:
    jpeg = tmp_path / "photo.jpg"
    _jpeg(jpeg)
    text = tmp_path / "note.txt"
    text.write_text("x", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    heic = tmp_path / "scan.heic"
    heic.write_bytes(b"heic")
    moment = datetime(2022, 3, 4, 5, 6, 7, tzinfo=UTC)
    calls: list[list[str]] = []

    def runner(command: list[str]) -> bool:
        calls.append(command)
        return True

    assert write_file_metadata_and_offset(jpeg, moment, exiftool="exiftool", runner=runner) is True
    assert calls == []
    assert read_exif(jpeg).naive == datetime(2022, 3, 4, 5, 6, 7)

    assert write_file_metadata_and_offset(video, moment, exiftool="exiftool", runner=runner) is True
    assert calls[-1][2].startswith("-QuickTime:CreationDate=")
    assert write_file_metadata_and_offset(heic, moment, exiftool="exiftool", runner=runner) is True
    assert calls[-1][2].startswith("-AllDates=")
    assert write_file_metadata_and_offset(text, moment, exiftool="exiftool", runner=runner) is False
    assert write_file_metadata_and_offset(video, moment, exiftool=None, runner=runner) is False


def test_gps_rational_and_round_trip(tmp_path: Path) -> None:
    assert degrees_to_dms_rational(37.5) == ((37, 1), (30, 1), (0, 10000))
    assert degrees_to_dms_rational(-122.25)[0] == (122, 1)
    assert degrees_to_dms_rational(-122.25)[1] == (15, 1)
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    assert write_geodata(path, 37.5, -122.25) is True
    loaded = piexif.load(str(path))
    assert loaded["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert loaded["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"W"
    assert loaded["GPS"][piexif.GPSIFD.GPSLatitude][0][0] == 37
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")
    assert write_geodata(note, 1.0, 2.0) is False


def test_filesystem_time_and_birth_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    moment = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "name", "posix")

    def runner(command: list[str]) -> bool:
        calls.append(command)
        return True

    assert set_filesystem_times(path, moment, setfile="SetFile", runner=runner) is True
    assert abs(path.stat().st_mtime - moment.timestamp()) < 1
    assert calls[0] == setfile_command("SetFile", path, moment)
    assert calls[0][1:3] == ["-d", "01/02/2020 03:04:05"]

    monkeypatch.setattr(sys, "platform", "linux")
    assert set_birthtime(path, moment, setfile="SetFile", runner=runner) is False


def test_windows_filetime_epoch() -> None:
    low, high = windows_filetime(0)
    assert (high << 32) | low == 11644473600 * 10_000_000


def test_write_counts_are_separate() -> None:
    counts = WriteCounts()
    tally_write(counts, filesystem_ok=True, metadata_ok=True)
    tally_write(counts, filesystem_ok=True, metadata_ok=False)
    tally_write(counts, filesystem_ok=True, metadata_ok=None)
    counts.skipped = 1
    assert counts.filesystem_ok == 3
    assert counts.metadata_ok == 1
    assert counts.metadata_failed == 1
    message = timezone_write_message(counts)
    assert "Updated filesystem times on 3 files." in message
    assert "Wrote metadata on 1." in message
    assert "1 metadata write failed." in message
    assert "1 file had no time." in message


def test_snapshot_mode() -> None:
    assert snapshot_mode(Path("a.jpg")) == "piexif"
    assert snapshot_mode(Path("a.tif")) == "piexif"
    assert snapshot_mode(Path("a.mov")) == "copy"
    assert snapshot_mode(Path("a.heic")) == "copy"
    assert snapshot_mode(Path("a.png")) == "copy"
    assert snapshot_mode(Path("a.txt")) == "times"
