"""File-time writes and transactional timezone reloads."""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from textual.widgets import Button

from conftest import wait_for_folder_load
from tui_renamer.app import FileTable, RenamerApp, TimezoneScreen
from tui_renamer.metadata import inspect_file, read_exif, write_image_exif_datetime
from tui_renamer.scan_bits import PendingScan
from tui_renamer.table_view import TableView


async def test_filename_date_apply_and_refresh_keep_working_position(tmp_path: Path):
    for index in range(36):
        (tmp_path / f"file-{index:02}.jpg").write_text("fixture")
    photo = tmp_path / "2024-01-15-142500.jpg"
    Image.new("RGB", (16, 16), "red").save(photo)
    os.utime(photo, (1700000000, 1700000000))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    app.table_view = TableView(extension="jpg", descending=True)
    async with app.run_test(size=(120, 30)) as pilot:
        await wait_for_folder_load(app, pilot)
        row = app.files.index(photo)
        app.selected = {row}
        app.manual[row] = "still-staged.jpg"
        await pilot.press("D")
        app._move_to(row)
        await pilot.pause()
        table = app.query_one(FileTable)
        assert table.scroll_y > 0
        scroll_y = table.scroll_y

        await pilot.press("enter", "y")
        await pilot.pause()
        assert app.date_mode
        assert app.files[app._cursor] == photo
        assert table.scroll_y == scroll_y
        assert not app.cached_date_plan().todo

        await pilot.press("r")
        await wait_for_folder_load(app, pilot)
        assert app.date_mode
        assert app.files[app._cursor] == photo
        assert table.scroll_y == scroll_y
        assert app.table_view == TableView(extension="jpg", descending=True)
        assert app.rules.source_tz == "UTC"
        assert app.selected == {row}
        assert app.manual == {row: "still-staged.jpg"}


async def test_rename_apply_follows_file_and_keeps_unrelated_staging(tmp_path: Path):
    source = tmp_path / "source.txt"
    other = tmp_path / "other.txt"
    for path in (source, other):
        path.write_text("fixture")
    for index in range(36):
        (tmp_path / f"file-{index:02}.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await wait_for_folder_load(app, pilot)
        index = app.files.index(source)
        app._move_to(index)
        app.selected = {index}
        app.manual = {index: "a-first.txt"}
        app.locked = {app.files.index(other): other.name}
        app.refresh_view()
        await pilot.pause()
        table = app.query_one(FileTable)
        assert table.scroll_y > 0

        await pilot.press("enter", "y")
        await pilot.pause()
        assert app.files[app._cursor] == tmp_path / "a-first.txt"
        assert app.selected == {app._cursor}
        assert not app.manual
        assert app.locked == {app.files.index(other): other.name}
        assert table.scroll_y == 0


async def test_filename_dates_survive_metadata_write_and_invalidate_plan(tmp_path: Path):
    photo = tmp_path / "2024-01-15-142500.jpg"
    Image.new("RGB", (16, 16), "red").save(photo)
    os.utime(photo, (1700000000, 1700000000))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_for_folder_load(app, pilot)
        app.action_preview()
        await app.workers.wait_for_complete()
        before = photo.stat().st_mtime
        plan = app.cached_date_plan()
        assert plan.todo
        target = datetime(2024, 1, 15, 14, 25, tzinfo=timezone.utc)
        app._do_dates(list(plan.todo))
        assert photo.stat().st_mtime == pytest.approx(target.timestamp(), abs=0.001)
        assert read_exif(photo).naive == target.replace(tzinfo=None)
        updated = app.cached_date_plan()
        assert updated is not plan
        assert not updated.todo
        assert updated.same[0].cur == pytest.approx(target.timestamp())
        app.action_undo()
        await pilot.pause()
        assert photo.stat().st_mtime == pytest.approx(before, abs=0.001)


@pytest.mark.parametrize("finish", ["success", "cancel", "error"])
@pytest.mark.parametrize("selected", ["America/New_York", "Local System"])
async def test_timezone_reload_preserves_staging_and_commits_only_on_success(
    tmp_path: Path, monkeypatch, finish: str, selected: str
):
    photo = tmp_path / "2024-01-15-142500.jpg"
    Image.new("RGB", (16, 16), "red").save(photo)
    original = photo.read_bytes()
    original_mtime = photo.stat().st_mtime_ns
    app = RenamerApp(tmp_path)
    old_zone = "UTC" if selected == "Local System" else None
    new_zone = None if selected == "Local System" else selected
    app.rules.source_tz = old_zone
    release = Event()
    started = Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_for_folder_load(app, pilot)
            app.selected.add(0)
            app.manual[0] = "manual.jpg"
            app.locked[0] = "locked.jpg"
            app._cursor = 0
            old_facts = app._facts.copy()
            if finish == "success":
                (tmp_path / "000-earlier.txt").write_text("new fixture")

            def reader(path, rules, zone):
                assert rules.source_tz == new_zone
                started.set()
                assert release.wait(10)
                if finish == "error":
                    raise OSError("fixture read failed")
                return inspect_file(path, rules, zone)

            monkeypatch.setattr("tui_renamer.app.inspect_file", reader)
            app.action_timezone()
            await pilot.pause()
            assert isinstance(app.screen, TimezoneScreen)
            app.screen.dismiss(selected)
            await pilot.pause()
            assert await asyncio.to_thread(started.wait, 2)
            assert app._loading
            assert app.rules.source_tz == old_zone
            assert app.query_one("#act-apply", Button).is_disabled
            await pilot.resize_terminal(90, 30)
            if finish == "cancel":
                await pilot.press("escape")
                assert not app._loading
            release.set()
            await wait_for_folder_load(app, pilot)
            assert app.rules.source_tz == (new_zone if finish == "success" else old_zone)
            index = app.files.index(photo)
            assert app._cursor == index
            assert app.selected == {index}
            assert app.manual == {index: "manual.jpg"}
            assert app.locked == {index: "locked.jpg"}
            if finish != "success":
                assert app._facts == old_facts
            assert photo.read_bytes() == original
            assert photo.stat().st_mtime_ns == original_mtime
    finally:
        release.set()


@pytest.mark.parametrize("action", ["timezone", "scan"])
async def test_metadata_writes_preserve_target_mtime_and_undo(tmp_path: Path, action: str):
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 16), "red").save(photo)
    target = datetime(2020, 1, 15, 14, 25, tzinfo=timezone.utc)
    if action == "timezone":
        assert write_image_exif_datetime(photo, target)
    os.utime(photo, (1700000000, 1700000000))
    before = photo.stat().st_mtime
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "Europe/Paris"
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_for_folder_load(app, pilot)
        app.action_preview()
        await app.workers.wait_for_complete()
        if action == "timezone":
            app._do_timezone([0])
        else:
            app._do_apply([], [PendingScan(photo, moment=target, description="fixture note")])
        assert photo.stat().st_mtime == pytest.approx(target.timestamp(), abs=0.001)
        assert read_exif(photo).naive is not None
        app.action_undo()
        await pilot.pause()
        assert photo.stat().st_mtime == pytest.approx(before, abs=0.001)
