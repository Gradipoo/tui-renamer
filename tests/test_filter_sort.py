"""Table view changes preserve file identity and the existing Apply boundary."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, DataTable, Input

from tui_renamer.app import ConfirmScreen, FilterSortScreen, RenamerApp
from tui_renamer.core import DateCandidate, FileFacts
from tui_renamer.scan_bits import PendingScan
from tui_renamer.table_view import TableView


def files(folder: Path) -> None:
    for name in ("a.jpg", "b.png", "c.jpg", "d.png"):
        (folder / name).write_bytes(b"fixture")


async def test_sort_filter_preserve_staging_and_cursor(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.selected = {0, 1}
        app.locked = {0: "locked.jpg"}
        app.manual = {1: "manual.png"}
        app.scan_suffix[str(app.files[2])] = ["trip"]
        app._cursor = 2
        before = app._rename_pairs()
        app.table_view = TableView(extension="jpg", descending=True)
        app.refresh_view()
        await pilot.pause()
        assert app._visible == [2, 0]
        assert app._cursor == 2
        assert app.query_one(DataTable).cursor_row == 0
        assert app._rename_pairs() == before
        assert app.selected == {0, 1}
        assert app.manual == {1: "manual.png"}
        assert "Hidden selected:1" in app.sub_title
        assert "1 hidden selected" in str(app.query_one("#view-open", Button).label)
        app.action_select_all()
        assert app.selected == {0, 1, 2}
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "a.jpg",
            "b.png",
            "c.jpg",
            "d.png",
        ]


async def test_navigation_search_and_range_follow_visible_order(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.table_view = TableView(extension="jpg", descending=True)
        app.refresh_view()
        app.action_goto_top()
        await pilot.pause()
        assert app._cursor == 2
        app.action_visual()
        await pilot.press("j")
        await pilot.pause()
        assert app._cursor == 0
        assert app.selected == {0, 2}
        app.action_visual()
        app.action_goto_top()
        await pilot.pause()
        await pilot.press("/", "j", "p", "g", "enter")
        await pilot.pause()
        assert app._cursor == 2
        await pilot.press("n")
        await pilot.pause()
        assert app._cursor == 0
        await pilot.press("N")
        await pilot.pause()
        assert app._cursor == 2
        assert app._hit_indexes() == [2, 0]


async def test_empty_results_have_no_cursor_actions(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.table_view = TableView(extension="mov")
        app.refresh_view()
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 0
        assert "No files match" in app._detail_text().plain
        app.action_toggle_select()
        app.action_insert()
        app.action_lock()
        app.action_scan_review()
        assert app.selected == set()
        assert app.locked == {}
        assert app._targets() == []
        assert len(app.screen_stack) == 1
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, FilterSortScreen)
        app.screen.query_one("#view-reset", Button).press()
        await pilot.pause()
        assert app._visible == [0, 1, 2, 3]


async def test_panel_combines_filters_and_can_cancel(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.manual[2] = "trip.jpg"
        app.scan_pending[str(app.files[2])] = PendingScan(app.files[2], description="Trip")
        await pilot.click("#view-open")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FilterSortScreen)
        screen.query_one("#filter-changed", Checkbox).value = True
        screen.query_one("#filter-pending", Checkbox).value = True
        screen.query_one("#view-extension", Input).value = ".JPG"
        screen.query_one("#view-show", Button).press()
        await pilot.pause()
        assert app._visible == [2]
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app._visible == [2]


@pytest.mark.parametrize("descending", [False, True])
async def test_capture_sort_keeps_missing_dates_last(tmp_path: Path, descending: bool) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        facts = {}
        for index, path in enumerate(app.files):
            moment = datetime(2020 + index, 1, 1, tzinfo=timezone.utc)
            label = "EXIF DateTimeOriginal" if index in {0, 2} else "birth"
            facts[path] = FileFacts(
                candidates=[
                    DateCandidate(label, "", moment, capture=index in {0, 2}),
                ]
            )
        app.facts_for = facts.__getitem__
        app.table_view = TableView(sort="capture", descending=descending)
        app.refresh_view()
        await pilot.pause()
        assert app._visible[:2] == ([2, 0] if descending else [0, 2])
        assert set(app._visible[2:]) == {1, 3}


@pytest.mark.parametrize("mode", ["rename", "dates", "mtime", "timezone", "gps"])
async def test_confirmation_identifies_hidden_targets(tmp_path: Path, mode: str) -> None:
    for name in ("2024-01-01.jpg", "2024-01-02.tif"):
        (tmp_path / name).write_bytes(b"fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.selected = {0, 1}
        app.manual[1] = "hidden.png"
        app.table_view = TableView(extension="jpg")
        app.refresh_view()
        await pilot.pause()
        if mode == "dates":
            app.date_mode = True
            app.action_apply()
        elif mode == "mtime":
            app.mtime_mode = True
            app.action_apply()
        elif mode == "timezone":
            app.action_write_timezone()
        elif mode == "gps":
            app.action_write_geodata()
        else:
            app.action_apply()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "Includes hidden files (1): 2024-01-02.tif" in app.screen.question
        await pilot.press("n")
        await pilot.pause()
        assert (tmp_path / "2024-01-02.tif").read_bytes() == b"fixture"


async def test_changing_filter_updates_after_manual_edit(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.manual[2] = "trip.jpg"
        app.table_view = TableView(frozenset({"changed"}))
        app.refresh_view()
        await pilot.pause()
        assert app._visible == [2]
        app.manual.clear()
        app.refresh_view()
        await pilot.pause()
        assert app._visible == []


async def test_sorted_apply_keeps_collision_order_and_undo(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("first")
    (tmp_path / "b.txt").write_text("second")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.manual = {0: "trip.txt", 1: "trip.txt"}
        app.table_view = TableView(descending=True)
        app.refresh_view()
        await pilot.pause()
        assert app._visible == [1, 0]
        app.action_apply()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert (tmp_path / "trip.txt").read_text() == "first"
        assert (tmp_path / "trip-01.txt").read_text() == "second"
        app.action_undo()
        await pilot.pause()
        assert (tmp_path / "a.txt").read_text() == "first"
        assert (tmp_path / "b.txt").read_text() == "second"


async def test_warning_sort_does_not_recompute_neighbors_in_display_order(tmp_path: Path) -> None:
    files(tmp_path)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        facts = {}
        for index, path in enumerate(app.files):
            moment = datetime(2020, 1, 1, tzinfo=timezone.utc)
            candidates = [DateCandidate("EXIF", "", moment, capture=True)]
            if index == 2:
                candidates.append(
                    DateCandidate(
                        "Filename", "", datetime(2021, 1, 1, tzinfo=timezone.utc), capture=True
                    )
                )
            facts[path] = FileFacts(winner_source="E", winner_instant=moment, candidates=candidates)
        app.facts_for = facts.__getitem__
        app.refresh_view()
        neighbors = app._neighbors[:]
        app.table_view = TableView(sort="warning")
        app.refresh_view()
        await pilot.pause()
        assert app._visible == [2, 0, 1, 3]
        assert app._neighbors == neighbors
        app.action_goto_top()
        await pilot.pause()
        assert app._cursor == 2
        app.table_view = TableView(sort="warning", descending=True)
        app.refresh_view()
        await pilot.pause()
        assert app._visible[-1] == 2
        assert app._neighbors == neighbors


async def test_sort_keeps_highlight_scrolled_into_view(tmp_path: Path, monkeypatch) -> None:
    for index in range(100):
        (tmp_path / f"{index:03}.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app._move_to(95)
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.scroll_y > 0
        # Deliver old scroll restorations late, as on a busy CI runner.
        delayed = []
        after_refresh = table.call_after_refresh

        def delay_restore(callback, *args, **kwargs):
            if callback == app._restore_table_scroll:
                delayed.append((callback, args, kwargs))
                return True
            return after_refresh(callback, *args, **kwargs)

        monkeypatch.setattr(table, "call_after_refresh", delay_restore)
        table.call_after_refresh(
            app._restore_table_scroll, table.scroll_x, table.scroll_y, app._scroll_restore_gen
        )
        app.action_filter_sort()
        await pilot.pause()
        app.screen.query_one("#view-descending", Checkbox).value = True
        app.screen.query_one("#view-show", Button).press()
        await pilot.pause()
        assert app._cursor == 95
        assert table.cursor_row == 4
        assert delayed
        for callback, args, kwargs in delayed:
            callback(*args, **kwargs)
        await pilot.pause()
        assert table.scroll_y <= 4
