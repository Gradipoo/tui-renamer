"""Batch scope and explicit timestamp assignment in earliest-date mode."""

import os
from datetime import datetime, timezone

import pytest
from textual.widgets import Select

from conftest import wait_for_folder_load, wait_for_reconcile_load
from tui_renamer.app import RenamerApp
from tui_renamer.reconcile_ui import ChooseReconcileDateScreen


@pytest.mark.parametrize("selection", [{0, 1}, set()])
async def test_chosen_date_only_stages_for_targets(tmp_path, selection):
    for name in ("2000-01-02-030405-a.txt", "2001-01-02-030405-b.txt", "2002-01-02-030405-c.txt"):
        path = tmp_path / name
        path.write_text("fixture")
        os.utime(path, (1700000000, 1700000000))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 42)) as pilot:
        await wait_for_folder_load(app, pilot)
        await pilot.press("R")
        await wait_for_reconcile_load(app, pilot)
        app.selected = selection.copy()
        app.refresh_view()
        assert all(row.status == "Update" for row in app._reconcile_rows)
        await pilot.press("C")
        assert isinstance(app.screen, ChooseReconcileDateScreen)
        assert app.screen.count == (len(selection) or 1)
        app.screen.query_one("#choose-date-source", Select).value = 0
        await pilot.pause()
        await pilot.click("#choose-date-stage")
        await pilot.pause()
        assert not app.journal.batches
        assert set(app._reconcile_assigned) == {app.files[index] for index in selection or {0}}
        assert app._reconcile_rows[1].date.startswith("2000" if selection else "2001")
        assert app._reconcile_rows[2].date.startswith("2002")
        if selection:
            await pilot.press("enter", "y")
            wanted = datetime(2000, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
            assert (tmp_path / "2000-01-02-030405-b.txt").stat().st_mtime == wanted
            assert (tmp_path / "2002-01-02-030405-c.txt").stat().st_mtime == 1700000000
