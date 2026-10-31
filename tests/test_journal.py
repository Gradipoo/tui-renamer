"""Undo tests for renames and metadata snapshots."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from tui_renamer.core import rename_paths
from tui_renamer.journal import Batch, Journal, RenameOp
from tui_renamer.metadata import read_exif, write_image_exif_datetime

UTC = timezone.utc


def _jpeg(path: Path) -> None:
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path, format="JPEG")


def test_undo_rename_restores_in_reverse_order(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    outcome = rename_paths([(second, "c.txt"), (first, "b.txt")])
    journal = Journal(tmp_path / "undo")
    journal.push(Batch("rename", [RenameOp(src, dst) for src, dst in outcome.done]))
    result = journal.undo()
    assert result is not None
    assert result.restored == 2
    assert first.read_text(encoding="utf-8") == "a"
    assert second.read_text(encoding="utf-8") == "b"
    assert journal.undo() is None


def test_undo_conflict_suffix(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    outcome = rename_paths([(first, "same.txt"), (second, "same.txt")])
    journal = Journal(tmp_path / "undo")
    journal.push(Batch("rename", [RenameOp(src, dst) for src, dst in outcome.done]))
    journal.undo()
    assert first.read_text(encoding="utf-8") == "a"
    assert second.read_text(encoding="utf-8") == "b"


def test_empty_batch_is_not_stored(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "undo")
    journal.push(Batch("rename"))
    assert journal.undo() is None


def test_exif_undo_restores_the_previous_segment(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    first = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
    second = datetime(2021, 6, 7, 8, 9, 10, tzinfo=UTC)
    assert write_image_exif_datetime(path, first)
    old_mtime = path.stat().st_mtime
    journal = Journal(tmp_path / "undo")
    op = journal.snapshot(path)
    assert op.had_exif is True
    assert op.exif_blob
    assert write_image_exif_datetime(path, second)
    os.utime(path, (second.timestamp(), second.timestamp()))
    births: list[float] = []
    journal.push(Batch("timezone", [op]))
    result = journal.undo(restore_birth=lambda _path, birth: births.append(birth))
    assert result is not None
    assert result.notes == []
    assert read_exif(path).naive == datetime(2020, 1, 2, 3, 4, 5)
    assert abs(path.stat().st_mtime - old_mtime) < 1
    if op.birth is not None:
        assert births == [op.birth]


def test_exif_undo_removes_a_segment_that_was_added(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    _jpeg(path)
    journal = Journal(tmp_path / "undo")
    op = journal.snapshot(path)
    op.had_exif = False
    op.exif_blob = None
    write_image_exif_datetime(path, datetime(2022, 2, 2, 2, 2, 2, tzinfo=UTC))
    journal.push(Batch("timezone", [op]))
    journal.undo()
    assert read_exif(path).naive is None


def test_failed_copy_is_reported_and_times_still_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "clip.mov"
    path.write_bytes(b"original")
    old = datetime(2019, 1, 1, tzinfo=UTC).timestamp()
    os.utime(path, (old, old))
    journal = Journal(tmp_path / "undo")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tui_renamer.journal.shutil.copy2", boom)
    op = journal.snapshot(path)
    assert op.copy_failed is True
    path.write_bytes(b"changed")
    os.utime(path, (datetime(2024, 1, 1, tzinfo=UTC).timestamp(),) * 2)
    journal.push(Batch("timezone", [op]))
    result = journal.undo()
    assert result is not None
    assert any("could not be restored" in note for note in result.notes)
    assert path.read_bytes() == b"changed"
    assert abs(path.stat().st_mtime - old) < 1


def test_cleanup_removes_the_temp_copy(tmp_path: Path) -> None:
    path = tmp_path / "clip.mov"
    path.write_bytes(b"video")
    folder = tmp_path / "undo"
    journal = Journal(folder)
    op = journal.snapshot(path)
    assert op.backup is not None and op.backup.exists()
    journal.cleanup()
    assert not folder.exists()
