"""Cancellable folder reads and atomic installation of background results."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event, Lock, Thread

from textual.widgets import Button, Static

from tui_renamer.app import FolderLoadFinished, FolderLoadProgress, RenamerApp
from tui_renamer.core import FileFacts, RenameRules
from tui_renamer.folder_load import FolderSnapshot, load_folder
from tui_renamer.metadata import inspect_file


async def wait_loaded(app: RenamerApp, pilot) -> None:
    await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
    await pilot.pause()
    assert not app._loading


def test_cancel_returns_without_waiting_for_inflight_reads(tmp_path: Path) -> None:
    for index in range(20):
        (tmp_path / f"{index:02}.txt").write_text("fixture")
    cancel = Event()
    release = Event()
    started = Event()
    lock = Lock()
    calls = 0
    result = []

    def reader(path, rules, zone):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 4:
                started.set()
        assert release.wait(5)
        return FileFacts()

    thread = Thread(
        target=lambda: result.append(
            load_folder(tmp_path, RenameRules(), cancel, lambda *_: None, reader=reader)
        )
    )
    thread.start()
    try:
        assert started.wait(3)
        cancel.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert result == [None]
        assert calls == 4
    finally:
        release.set()
        thread.join(timeout=3)


async def test_startup_remains_responsive_with_progress_and_cancel(tmp_path: Path, monkeypatch):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("fixture")
    release = Event()
    started = Event()

    def reader(path, rules, zone):
        if path.name == "b.txt":
            started.set()
            assert release.wait(5)
        return inspect_file(path, rules, zone)

    monkeypatch.setattr("tui_renamer.app.inspect_file", reader)
    app = RenamerApp(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            assert await asyncio.to_thread(started.wait, 3)
            await pilot.pause()
            assert app._loading
            assert "1/2" in str(app.query_one("#folder-progress", Static).render())
            assert app.query_one("#act-apply", Button).is_disabled
            assert not app.check_action("apply", ())
            generation = app._folder_generation
            await pilot.resize_terminal(90, 30)
            await pilot.press("enter", "y")
            # Enter on the focused Cancel button cancels without applying anything.
            assert not app._loading
            assert not app.files
            assert not app.journal.batches
            assert (tmp_path / "a.txt").read_text() == "fixture"
            app.on_folder_load_finished(
                FolderLoadFinished(generation, FolderSnapshot([tmp_path / "stale"], {}, set()))
            )
            assert not app.files
            release.set()
            await wait_loaded(app, pilot)
            await pilot.press("r")
            await wait_loaded(app, pilot)
            assert [path.name for path in app.files] == ["a.txt", "b.txt"]
            assert not app.query_one("#folder-load-bar").display
    finally:
        release.set()


async def test_cancelled_refresh_keeps_staging_and_rejects_old_results(tmp_path: Path, monkeypatch):
    photo = tmp_path / "photo.txt"
    photo.write_text("fixture")
    app = RenamerApp(tmp_path)
    release = Event()
    started = Event()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_loaded(app, pilot)
            app.selected.add(0)
            app.locked[0] = "locked.txt"
            app.manual[0] = "manual.txt"
            app.scan_suffix[str(photo)] = ["tag"]
            old_facts = app._facts.copy()

            def reader(path, rules, zone):
                started.set()
                assert release.wait(5)
                return inspect_file(path, rules, zone)

            monkeypatch.setattr("tui_renamer.app.inspect_file", reader)
            await pilot.press("r")
            await pilot.press("y")
            assert await asyncio.to_thread(started.wait, 3)
            generation = app._folder_generation
            await pilot.press("escape")
            assert not app._loading
            assert app.files == [photo]
            assert app.selected == {0}
            assert app.locked == {0: "locked.txt"}
            assert app.manual == {0: "manual.txt"}
            assert app.scan_suffix[str(photo)] == ["tag"]
            assert app._facts == old_facts
            # Restart while a cancelled reader is still finishing.
            release.set()
            await pilot.press("r", "y")
            app.on_folder_load_progress(FolderLoadProgress(generation, 99, 99))
            app.on_folder_load_finished(FolderLoadFinished(generation, None, "stale error"))
            await wait_loaded(app, pilot)
            assert app.files == [photo]
            assert not app.selected and not app.locked and not app.manual
            assert app.scan_suffix[str(photo)] == ["tag"]
            assert "stale error" not in app._message
            assert photo.read_text() == "fixture"
    finally:
        release.set()


async def test_failed_load_keeps_current_files_and_can_retry(tmp_path: Path, monkeypatch):
    photo = tmp_path / "photo.txt"
    photo.write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_loaded(app, pilot)
        app.manual[0] = "manual.txt"

        def fail(*args):
            raise OSError("cannot read fixture [/bold]")

        monkeypatch.setattr("tui_renamer.app.inspect_file", fail)
        await pilot.press("r", "y")
        await wait_loaded(app, pilot)
        assert app.files == [photo]
        assert app.manual == {0: "manual.txt"}
        assert "cannot read fixture" in app._message
        assert not app.query_one("#body").disabled
        monkeypatch.setattr("tui_renamer.app.inspect_file", inspect_file)
        await pilot.press("r", "y")
        await wait_loaded(app, pilot)
        assert not app.manual
        assert photo.read_text() == "fixture"


async def test_quit_does_not_wait_for_blocked_metadata(tmp_path: Path, monkeypatch):
    (tmp_path / "photo.txt").write_text("fixture")
    release = Event()
    started = Event()

    def reader(*args):
        started.set()
        assert release.wait(5)
        return FileFacts()

    monkeypatch.setattr("tui_renamer.app.inspect_file", reader)
    app = RenamerApp(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            assert await asyncio.to_thread(started.wait, 3)
            await pilot.press("q")
        assert app._folder_cancel.is_set()
    finally:
        release.set()


def test_folder_listing_errors_are_not_empty_snapshots(tmp_path: Path) -> None:
    import pytest

    from tui_renamer.core import list_files

    missing = tmp_path / "missing"
    assert list_files(missing) == []
    snapshot = load_folder(missing, RenameRules(), Event(), lambda *_: None, strict=False)
    assert snapshot is not None and not snapshot.files
    with pytest.raises(OSError):
        load_folder(missing, RenameRules(), Event(), lambda *_: None)


async def test_queued_load_messages_ignore_removed_widgets(tmp_path: Path, monkeypatch):
    (tmp_path / "photo.txt").write_text("fixture")
    release = Event()
    started = Event()

    def reader(*args):
        started.set()
        assert release.wait(5)
        return FileFacts()

    monkeypatch.setattr("tui_renamer.app.inspect_file", reader)
    app = RenamerApp(tmp_path)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            assert await asyncio.to_thread(started.wait, 3)
            generation = app._folder_generation
            await app.query_one("#folder-load-bar").remove()
            app.on_folder_load_progress(FolderLoadProgress(generation, 1, 1))
            app.on_folder_load_finished(
                FolderLoadFinished(generation, FolderSnapshot([], {}, set()))
            )
            assert app._loading  # No late message tried to reinstall a detached view.
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
    finally:
        release.set()
