"""Earliest-date preview workers must stay independent of navigation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

from textual.widgets import Button, Static

from conftest import wait_for_folder_load, wait_for_reconcile_load
from tui_renamer.app import ReconcileLoadFinished, RenamerApp


async def test_slow_preview_allows_navigation_and_selection(tmp_path: Path, monkeypatch) -> None:
    for filename in ("2020-01-01-000000-a.txt", "2020-01-01-000000-b.txt"):
        (tmp_path / filename).write_text("fixture")
    started = Event()
    release = Event()
    reads: list[Path] = []

    def slow_metadata(path: Path, _tool: str | None) -> dict[str, str | None]:
        reads.append(path)
        started.set()
        assert release.wait(10)
        return {}

    monkeypatch.setattr("tui_renamer.app.reconcile_metadata_fields", slow_metadata)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        try:
            await pilot.press("R")
            assert await asyncio.to_thread(started.wait, 2)
            assert app.reconcile_mode and app._reconcile_loading
            assert app.query_one("#act-apply", Button).disabled
            assert "Loading earliest-date preview" in str(app.query_one("#status", Static).render())
            await pilot.press("down", "space")
            assert app._cursor == 1
            assert app.selected == {1}
            await pilot.press("enter")
            assert not app.journal.batches
        finally:
            release.set()
        await wait_for_reconcile_load(app, pilot)
        assert not app.query_one("#act-apply", Button).disabled
        assert app._cursor == 1
        assert app.selected == {1}
        await pilot.press("up", "space")
        assert sorted(reads) == sorted(app.files)


async def test_escaping_slow_preview_preserves_view_and_ignores_late_result(
    tmp_path: Path, monkeypatch
) -> None:
    for filename in ("2020-01-01-000000-a.txt", "2020-01-01-000000-b.txt"):
        (tmp_path / filename).write_text("fixture")
    started = Event()
    release = Event()

    def slow_metadata(_path: Path, _tool: str | None) -> dict[str, str | None]:
        started.set()
        assert release.wait(10)
        return {}

    monkeypatch.setattr("tui_renamer.app.reconcile_metadata_fields", slow_metadata)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("D", "down", "space")
        previous_files = app.files.copy()
        try:
            await pilot.press("R")
            assert await asyncio.to_thread(started.wait, 2)
            generation = app._reconcile_generation
            await pilot.press("escape")
            assert app.date_mode and not app.reconcile_mode
            assert app._cursor == 1
            assert app.selected == {1}
            assert app.files == previous_files
            await pilot.press("R")
            assert app._reconcile_loading
            app.on_reconcile_load_finished(ReconcileLoadFinished(generation, []))
            assert app._reconcile_loading and not app._reconcile_files
        finally:
            release.set()
        await wait_for_reconcile_load(app, pilot)
        assert [file.path for file in app._reconcile_files] == previous_files
        assert app._cursor == 1
        assert app.selected == {1}
