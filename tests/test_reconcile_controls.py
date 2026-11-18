"""Controls and diff colors for earliest-date reconciliation."""

from __future__ import annotations

import os
from pathlib import Path

from textual.widgets import Button, Collapsible

from conftest import wait_for_folder_load, wait_for_reconcile_load
from tui_renamer.app import OptionToggle, RenamerApp
from tui_renamer.commands import COMMAND_SPECS, CommandContext, dim_reason


async def test_reconcile_option_and_diff_colors(tmp_path: Path) -> None:
    photo = tmp_path / "2020-01-02-030405-scan.txt"
    photo.write_text("fixture", encoding="utf-8")
    os.utime(photo, (1700000000, 1700000000))
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_for_folder_load(app, pilot)
        group = app.query_one("#group-metadata", Collapsible)
        toggle = app.query_one("#opt-reconcile_mode", OptionToggle)
        choose = app.query_one("#act-reconcile-date", Button)
        edit = app.query_one("#act-edit-reconcile", Button)
        assert not app.query_one("#reconcile-options").display
        group.collapsed = False
        await pilot.pause()
        await pilot.click("#opt-reconcile_mode")
        await wait_for_reconcile_load(app, pilot)
        assert toggle.value and app.reconcile_mode
        assert "earliest dates ON" in group.title
        assert str(choose.label) == "C Choose date"
        assert str(edit.label) == "I Edit highlighted file"
        assert app.query_one("#reconcile-options").display
        assert not app.query_one("#reconcile-options").disabled

        details = app._detail_text()
        removed = [
            details.plain[span.start : span.end]
            for span in details.spans
            if span.style == "red strike"
        ]
        added = [
            details.plain[span.start : span.end] for span in details.spans if span.style == "green"
        ]
        assert any(value.startswith("2023-11-14") for value in removed)
        assert any(value.startswith("2020-01-02") for value in added)
        fields = app._reconcile_table_rows()[0][4]
        assert fields.style == "green"
        assert "Mtime" in fields.plain

        await pilot.click("#opt-reconcile_mode")
        assert not app.reconcile_mode and not toggle.value
        assert not app.query_one("#reconcile-options").display


def test_reconcile_palette_actions_are_available_only_in_ready_mode() -> None:
    commands = {
        spec.run: spec
        for spec in COMMAND_SPECS
        if spec.run in {"edit_reconcile", "choose_reconcile_date"}
    }
    active = CommandContext(1, 0, 0, 0, False, False, reconcile_mode=True)
    loading = CommandContext(1, 0, 0, 0, False, False, reconcile_mode=True, reconcile_loading=True)
    inactive = CommandContext(1, 0, 0, 0, False, False)
    assert commands["choose_reconcile_date"].key == "C"
    for command in commands.values():
        assert dim_reason(command, active) == ""
        assert dim_reason(command, loading) == "dates are loading"
        assert dim_reason(command, inactive) == "earliest date mode required"
