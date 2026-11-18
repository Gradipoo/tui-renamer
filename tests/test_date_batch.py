"""Integration tests for the reviewed earliest-date batch."""

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from textual.widgets import Checkbox

from conftest import wait_for_folder_load, wait_for_reconcile_load
from tui_renamer.app import RenamerApp
from tui_renamer.date_batch import DateChange, apply_date_changes
from tui_renamer.journal import Journal
from tui_renamer.metadata import read_exif, write_image_exif_datetime
from tui_renamer.reconcile_ui import ReconcileScreen
from tui_renamer.table_view import TableView


@pytest.mark.parametrize("filename_wins", [False, True])
async def test_review_apply_and_undo_dates(tmp_path: Path, filename_wins: bool):
    original = tmp_path / "2020-01-02-030405-photo.jpg"
    target = original if filename_wins else tmp_path / "2010-01-02-030405-photo.jpg"
    moment = datetime(2020 if filename_wins else 2010, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    capture = datetime(2022 if filename_wins else 2010, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    modified = datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()
    Image.new("RGB", (8, 8), "red").save(original)
    assert write_image_exif_datetime(original, capture)
    os.utime(original, (modified, modified))

    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("R")
        await wait_for_reconcile_load(app, pilot)
        assert app.reconcile_mode and not isinstance(app.screen, ReconcileScreen)
        assert ("Filename" if filename_wins else "EXIF") in app._detail_text().plain
        assert original.exists()
        assert original.stat().st_mtime == modified

        await pilot.press("enter", "y")
        await wait_for_reconcile_load(app, pilot)
        assert app.reconcile_mode
        assert app._reconcile_rows[app._cursor].status == "Match"
        assert app.files[app._cursor] == target
        assert target.stat().st_mtime == moment.timestamp()
        assert read_exif(target).naive == moment.replace(tzinfo=None)
        app.action_undo()
        await wait_for_reconcile_load(app, pilot)
        assert original.stat().st_mtime == modified
        assert read_exif(original).naive == capture.replace(tzinfo=None)


def test_date_batch_never_overwrites_occupied_names(tmp_path: Path):
    path = tmp_path / "original.txt"
    target = tmp_path / "renamed.txt"
    path.write_text("original")
    target.write_text("occupied")
    journal = Journal(tmp_path / "undo")
    change = DateChange(path, target)
    batch, moved, _, errors = apply_date_changes(
        [change], journal, zone=timezone.utc, exiftool=None, setfile=None
    )
    assert errors and not moved and not batch.ops
    assert path.read_text() == "original"
    assert target.read_text() == "occupied"

    target.unlink()
    batch, moved, _, errors = apply_date_changes(
        [change], journal, zone=timezone.utc, exiftool=None, setfile=None
    )
    assert not errors and moved == {path: target}
    journal.push(batch)
    path.write_text("new file")
    undo = journal.undo()
    assert undo.restored == 0
    assert path.read_text() == "new file"
    assert target.read_text() == "original"


async def test_date_review_rejects_modified_source(tmp_path: Path):
    photo = tmp_path / "2000-01-01-120000-photo.jpg"
    Image.new("RGB", (8, 8), "red").save(photo)
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("R")
        await wait_for_reconcile_load(app, pilot)
        await pilot.press("enter")
        new_mtime = datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()
        os.utime(photo, (new_mtime, new_mtime))
        await pilot.press("y")
        await pilot.pause()
        assert photo.stat().st_mtime == new_mtime
        assert not app.journal.batches
        assert "changed during review" in app._message


async def test_filesystem_winner_can_be_resolved_in_single_file_editor(tmp_path: Path):
    photo = tmp_path / "scan.jpg"
    Image.new("RGB", (8, 8), "red").save(photo)
    moment = datetime(2000, 1, 2, tzinfo=timezone.utc)
    os.utime(photo, (moment.timestamp(), moment.timestamp()))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("R")
        await wait_for_reconcile_load(app, pilot)
        assert app._reconcile_rows[0].status == "Needs review"
        await pilot.press("I")
        assert isinstance(app.screen, ReconcileScreen)
        assert app.screen.query_one("#reconcile-capture", Checkbox).value
        await pilot.click("#reconcile-capture")
        await pilot.click("#reconcile-apply")
        await pilot.pause()
        assert app._reconcile_rows[0].status == "Update"
        assert photo.exists()
        await pilot.press("enter", "y")
        await wait_for_reconcile_load(app, pilot)
        renamed = tmp_path / "2000-01-02-000000-scan.jpg"
        assert renamed.exists()
        assert read_exif(renamed).naive is None
        assert app._reconcile_rows[app._cursor].status == "Match"


async def test_batch_applies_hidden_files_and_leaves_unresolved_rows(tmp_path: Path):
    files = [
        tmp_path / "2001-02-03-040506-visible.jpg",
        tmp_path / "2001-02-03-040506-hidden.png",
        tmp_path / "1999-02-03-partial.jpg",
    ]
    for path in files:
        Image.new("RGB", (8, 8), "red").save(path)
        os.utime(path, (1700000000, 1700000000))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    app.table_view = TableView(extension="jpg")
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("R")
        await wait_for_reconcile_load(app, pilot)
        assert sorted(row.status for row in app._reconcile_rows) == [
            "Needs review",
            "Update",
            "Update",
        ]
        await pilot.press("enter")
        assert "1 need review" in app.screen.question
        assert files[1].name in app.screen.question
        await pilot.press("y")
        wanted = datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone.utc).timestamp()
        assert [path.stat().st_mtime for path in files] == [wanted, wanted, 1700000000]
        assert app.reconcile_mode
        assert app.table_view.extension == "jpg"
