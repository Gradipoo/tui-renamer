"""Navigation races and bounded name planning during UI updates."""

from pathlib import Path

import pytest
from PIL import Image
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from tui_renamer.app import RenamerApp


async def test_refresh_checks_name_plan_once(tmp_path: Path, monkeypatch) -> None:
    for index in range(40):
        (tmp_path / f"file-{index:03}.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.selected.update(range(len(app.files)))
        original = app._names_key
        calls = 0

        def counted():
            nonlocal calls
            calls += 1
            return original()

        monkeypatch.setattr(app, "_names_key", counted)
        app.refresh_view()
        assert calls == 1


@pytest.mark.parametrize("handler", ["_fit_table_columns", "_end_rebuild"])
async def test_deferred_handlers_ignore_cursor_outside_live_rows(
    tmp_path: Path, handler: str
) -> None:
    (tmp_path / "file.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        # A deferred cursor watcher may not yet have clamped the coordinate.
        table.set_reactive(DataTable.cursor_coordinate, Coordinate(10, 0))
        if handler == "_end_rebuild":
            app._begin_rebuild()
        getattr(app, handler)()
        table.move_cursor(row=0)


async def test_rapid_navigation_with_refresh_and_resize(tmp_path: Path) -> None:
    for index in range(35):
        path = tmp_path / f"file-{index:03}.jpg"
        if index % 3:
            Image.new("RGB", (32, 24), "red").save(path)
        else:
            path.write_bytes(b"invalid image")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for _ in range(3):
            for _ in range(15):
                app.action_cursor_down()
            app.refresh_view()
            await pilot.resize_terminal(90, 30)
            for _ in range(10):
                app.action_cursor_up()
            await pilot.resize_terminal(120, 40)
            await pilot.press("j", "k")
            await pilot.pause()
            table = app.query_one(DataTable)
            assert app._cursor == app._visible[table.cursor_row]
            assert not app._rebuilding
            assert len(app.query("#preview-image")) <= 1


def test_scoped_plan_revalidates_after_edits_and_errors(tmp_path: Path) -> None:
    (tmp_path / "original.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    try:
        app.reload_files()
        with app._name_plan_scope():
            assert app.preview_for(0) == "original.txt"
            with app._name_plan_scope():
                assert app.preview_for(0) == "original.txt"
        app.manual[0] = "edited.txt"
        with app._name_plan_scope():
            assert app.preview_for(0) == "edited.txt"
        try:
            with app._name_plan_scope():
                raise ValueError("interrupted render")
        except ValueError:
            pass
        app.manual[0] = "updated.txt"
        assert app.preview_for(0) == "updated.txt"
        assert app._render_names is None
    finally:
        app.journal.cleanup()


def test_cold_media_scan_uses_bounded_readers(tmp_path: Path, monkeypatch) -> None:
    from threading import Barrier, Lock

    from tui_renamer.metadata import inspect_file

    for index in range(20):
        (tmp_path / f"file-{index:03}.jpg").write_bytes(b"invalid image")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    barrier = Barrier(4, timeout=5)
    lock = Lock()
    active = peak = calls = 0

    def inspect(path, rules, zone):
        nonlocal active, peak, calls
        with lock:
            active += 1
            peak = max(peak, active)
            calls += 1
        try:
            barrier.wait()
            return inspect_file(path, rules, zone)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("tui_renamer.app.inspect_file", inspect)
    app = RenamerApp(tmp_path)
    try:
        app.reload_files()
        assert peak == 4
        assert calls == 20
        for path in app.files:
            assert app.facts_for(path) == inspect_file(path, app.rules, app.target_zone())
        assert calls == 20  # Rendering reuses the prefetched metadata.
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    finally:
        app.journal.cleanup()


def test_name_cache_key_uses_stable_reader_identity(tmp_path: Path) -> None:
    (tmp_path / "original.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    try:
        app.reload_files()
        app.facts_for = RenamerApp.facts_for.__get__(app)
        first = app._names_key()
        app.facts_for = RenamerApp.facts_for.__get__(app)
        assert app._names_key() == first
    finally:
        app.journal.cleanup()
