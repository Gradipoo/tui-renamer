"""Pilot tests for panes, the palette, and one rename plus undo."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.coordinate import Coordinate
from textual.geometry import Region
from textual.widgets import Collapsible, DataTable, Input, OptionList, Static

from tui_renamer.app import (
    CommandScreen,
    OptionToggle,
    RenamerApp,
    TimezoneScreen,
    linux_stdin_deleted,
)


class FrozenDate(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        if tz is not None:
            return cls(2020, 1, 2, 3, 4, 5, tzinfo=tz)
        return cls(2020, 1, 2, 3, 4, 5)


def test_linux_terminal_gone(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(os, "readlink", lambda _path: "/dev/pts/3 (deleted)")
    assert linux_stdin_deleted() is True


def test_linux_terminal_still_there(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(os, "readlink", lambda _path: "/dev/pts/3")
    assert linux_stdin_deleted() is False


def test_other_platforms_ignore_the_linux_check(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert linux_stdin_deleted() is False


async def test_empty_folder_opens(tmp_path: Path) -> None:
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        detail = str(app.query_one("#detail-body").render())
        assert "No files" in detail


def _within(app: RenamerApp, widget_id: str) -> bool:
    node = app.focused
    while node is not None:
        if getattr(node, "id", None) == widget_id:
            return True
        node = node.parent
    return False


async def test_number_keys_zoom_panes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        assert app._zoom == "detail"
        assert _within(app, "detail")
        assert app.query_one("#files").display is False
        await pilot.press("3")
        assert app._zoom == "options"
        assert _within(app, "options")
        await pilot.press("4")
        assert app._zoom == "preview"
        assert _within(app, "preview-slot")
        assert app.query_one("#files").border_title == "1 Files"
        assert app.query_one("#detail").border_title == "2 Detail"
        assert app.query_one("#options").border_title == "3 Options"
        assert app.query_one("#preview-slot").border_title == "4 Preview"
        await pilot.press("4")
        assert app._zoom is None
        await pilot.press("1")
        assert app._zoom == "files"
        assert isinstance(app.focused, DataTable)
        await pilot.press("escape")
        assert app._zoom is None


def _row_keys(table: DataTable) -> list[str]:
    return [str(row.key.value) for row in table.ordered_rows]


def _sel(table: DataTable, row: int) -> str:
    return str(table.get_cell_at(Coordinate(row, 0))).strip()


async def _scroll_to_row(pilot, table: DataTable, row: int) -> float:
    table.move_cursor(row=row, scroll=True)
    for _ in range(40):
        await pilot.pause(0.05)
        if table.cursor_row == row and table.scroll_y > 0:
            return table.scroll_y
    table.scroll_to(y=float(row), animate=False)
    await pilot.pause()
    assert table.cursor_row == row
    assert table.scroll_y > 0
    return table.scroll_y


async def test_select_and_visual_keep_scroll_and_order(tmp_path: Path) -> None:
    for index in range(220):
        (tmp_path / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        scroll = await _scroll_to_row(pilot, table, 120)
        order = _row_keys(table)
        await pilot.press("space")
        await pilot.pause()
        assert table.cursor_row == 120
        assert table.scroll_y == scroll
        assert _row_keys(table) == order
        assert _sel(table, 120) == "*"
        assert _sel(table, 119) == ""
        assert _sel(table, 121) == ""
        await pilot.press("v")
        for _ in range(4):
            await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == 124
        assert table.scroll_y > 0
        assert _row_keys(table) == order
        assert app.selected == set(range(120, 125))
        assert _sel(table, 120) == "*"
        assert _sel(table, 124) == "*"
        assert _sel(table, 125) == ""
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert app._zoom == "files"
        scroll = await _scroll_to_row(pilot, table, 120)
        order = _row_keys(table)
        await pilot.press("space")
        await pilot.pause()
        assert table.cursor_row == 120
        assert table.scroll_y == scroll
        assert _row_keys(table) == order
        assert _sel(table, 120) == "*"


async def test_files_zoom_shows_full_names_then_narrows(tmp_path: Path) -> None:
    name = "summer-hike-along-the-ridge-trail.jpg"
    (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "short.txt").write_text("y", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=1)
        app._cursor = 1
        app.selected.add(1)
        app.refresh_view()
        await pilot.pause()
        shown = table.get_cell_at(Coordinate(1, 4))
        assert isinstance(shown, Text)
        assert "..." in shown.plain
        assert shown.plain != name
        narrow = app._name_width
        await pilot.press("1")
        await pilot.pause()
        assert app._zoom == "files"
        full = table.get_cell_at(Coordinate(1, 4))
        assert isinstance(full, Text)
        assert full.plain == name
        assert "..." not in full.plain
        assert app._name_width > narrow
        assert str(table.get_cell_at(Coordinate(1, 0))).strip() == "*"
        assert table.cursor_row == 1
        await pilot.press("escape")
        await pilot.pause()
        assert app._zoom is None
        back = table.get_cell_at(Coordinate(1, 4))
        assert isinstance(back, Text)
        assert "..." in back.plain
        assert app._name_width == narrow
        assert table.cursor_row == 1
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert app._zoom is None
        again = table.get_cell_at(Coordinate(1, 4))
        assert isinstance(again, Text)
        assert "..." in again.plain


async def test_escape_closes_find_then_zoom_then_mode(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.press("2")
        await pilot.press("/")
        await pilot.press("escape")
        assert app._find_open is False
        assert app._zoom == "detail"
        assert app.visual is True
        await pilot.press("escape")
        assert app._zoom is None
        assert app.visual is True
        await pilot.press("escape")
        assert app.visual is False


def _painted(widget) -> str:
    """Text drawn inside one widget. A clipped height drops the label."""
    crop = Region(0, 0, widget.size.width, max(widget.size.height, 1))
    return "".join(line.text for line in widget.render_lines(crop))


def _background(widget) -> str:
    crop = Region(0, 0, max(widget.size.width, 1), 1)
    for line in widget.render_lines(crop):
        for segment in line:
            style = segment.style
            if style is not None and style.bgcolor is not None:
                return style.bgcolor.name or str(style.bgcolor)
    return ""


async def test_date_toggle_checks_the_option(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        box = app.query_one("#opt-add_datetime", OptionToggle)
        assert box.value is False
        await pilot.press("d")
        assert box.value is True
        assert app.rules.add_datetime is True
        await pilot.click("#opt-add_datetime")
        assert app.rules.add_datetime is False
        assert box.value is False


async def test_filename_date_mode_diff_and_cache(tmp_path: Path) -> None:
    first = tmp_path / "2024-01-15-noon.txt"
    first.write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert app.date_mode is True
        rows = app._date_rows()
        assert len(rows) == 1
        new_cell = rows[0][6]
        assert "2024-01-15" in new_cell.plain
        # Verify plan caching
        plan1 = app.cached_date_plan()
        plan2 = app.cached_date_plan()
        assert plan1 is plan2
        detail = app._detail_text()
        assert "Filename Date Target" in detail.plain
        assert "target" in detail.plain
        assert "diff" in detail.plain


async def test_option_groups_collapse_and_keep_active_settings_visible(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for group in app.query("#options Collapsible"):
            assert group.collapsed
        await pilot.press("d", "c")
        await pilot.pause()
        on = app.query_one("#opt-add_datetime", OptionToggle)
        off = app.query_one("#opt-add_modtime", OptionToggle)
        assert on.has_class("-on")
        assert not off.has_class("-on")
        assert _background(on) != _background(off)
        cleanup = app.query_one("#group-cleanup", Collapsible)
        assert cleanup.collapsed
        assert "lower" in cleanup.title
        cleanup.collapsed = False
        await pilot.pause()
        for toggle in app.query(OptionToggle):
            chip = toggle.render()
            assert (
                chip.plain
                == f" {toggle.key_name} {toggle.caption} {'ON' if toggle.value else 'off'} "
            )
            assert toggle.tooltip
            assert "underline" in str(chip.spans[0].style)
        off.focus()
        await pilot.pause()
        assert _background(off) != _background(on)
        for width in (120, 72):
            await pilot.resize_terminal(width, 40)
            await pilot.pause()
            toggle = app.query_one("#opt-remove_caps", OptionToggle)
            toggle.scroll_visible(animate=False)
            await pilot.pause()
            assert toggle.size.height == 1
            assert "c lower ON" in _painted(toggle)
            assert app.query_one("#options").content_region.contains_region(toggle.region)
            bar = app.query_one("#batch-bar")
            assert bar.region.bottom <= app.size.height
            assert bar.content_region.contains_region(app.query_one("#act-apply").region)


async def test_commands_overlay_filters_and_closes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, CommandScreen)
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        await pilot.press(":")
        assert isinstance(app.screen, CommandScreen)
        await pilot.press("u", "n", "d", "o")
        screen = app.screen
        assert isinstance(screen, CommandScreen)
        assert any(reason == "nothing to undo" for _spec, reason in screen.shown)
        await pilot.press("question_mark")
        assert len(app.screen_stack) == 1
        await pilot.press("?")
        await pilot.press("t", "i", "m", "e", "z", "o", "n", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TimezoneScreen)
        assert not any(isinstance(screen, CommandScreen) for screen in app.screen_stack)


async def test_gg_and_end(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("G")
        assert app.query_one(DataTable).cursor_row == 2
        await pilot.press("g")
        assert "g then g" in str(app.query_one("#status").render())
        await pilot.press("g")
        assert app.query_one(DataTable).cursor_row == 0


async def test_enter_without_a_selection_does_not_rename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.app.datetime", FrozenDate)
    (tmp_path / "photo.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.press("enter")
        await pilot.pause()
        assert (tmp_path / "photo.txt").exists()
        assert not (tmp_path / "2020-01-02-030405-photo.txt").exists()
        assert "No changes" in app._message


def _is_dim(cell: object) -> bool:
    if not isinstance(cell, Text):
        return False
    style = cell.style
    if isinstance(style, Style) and style.dim:
        return True
    if "dim" in str(style):
        return True
    for span in cell.spans:
        if "dim" in str(span.style):
            return True
    return False


async def test_change_overview(tmp_path: Path) -> None:
    stem = "vacation-photo-from-the-camera-roll-extra-long-name-that-will-not-fit"
    source_name = f"2020-01-02-030405-{stem}.jpg"
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    (tmp_path / source_name).write_bytes(b"jpeg")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "1 of 2 previews differ" in app.sub_title
        table = app.query_one(DataTable)
        labels = [column.label.plain for column in table.columns.values()]
        assert labels[:4] == ["Sel", "Fit", "Flag", "Miss"]
        assert labels[-1] == "State"
        widths = {column.label.plain: column.width for column in table.columns.values()}
        assert widths["Fit"] == 3
        assert widths["Flag"] == 4
        assert widths["Miss"] == 4
        assert widths["State"] == 11
        assert widths["Sel"] == 3
        plain = table.get_cell_at(Coordinate(1, 4))
        assert _is_dim(plain)
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("n")
        assert table.cursor_row == 0
        shown = table.get_cell_at(Coordinate(0, 5))
        assert isinstance(shown, Text)
        assert "..." in shown.plain
        assert shown.plain != f"{stem}.jpg"
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.press("y")
        await pilot.pause()
        assert (tmp_path / f"{stem}.jpg").exists()
        assert not (tmp_path / source_name).exists()


async def test_warning_jump_timeline_and_detail(tmp_path: Path) -> None:
    from PIL import Image

    from tui_renamer.metadata import write_image_exif_datetime

    photo = tmp_path / "2020-06-01-120000-photo.jpg"
    Image.new("RGB", (8, 8), (20, 30, 40)).save(photo, format="JPEG")
    write_image_exif_datetime(photo, datetime(2020, 6, 1, 17, 0, tzinfo=timezone.utc))
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.rules.source_tz = "UTC"
        app._facts.clear()
        app.refresh_view()
        await pilot.pause()
        assert "W:1" in app.sub_title
        table = app.query_one(DataTable)
        assert table.get_cell_at(Coordinate(0, 1)).plain == "rn"
        assert table.get_cell_at(Coordinate(0, 2)).plain == "H"
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("!")
        assert table.cursor_row == 0
        detail = str(app.query_one("#detail-body").render())
        assert "Date source: EXIF" in detail
        assert "Warning: looks like a timezone offset" in detail
        assert "timezone" in detail.lower()
        assert "-5h" in detail
        assert "#" in str(app.query_one("#timeline").render())


async def test_rename_and_undo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.app.datetime", FrozenDate)
    (tmp_path / "photo.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.press("y")
        await pilot.pause()
        renamed = tmp_path / "2020-01-02-030405-photo.txt"
        assert renamed.exists()
        await pilot.press("u")
        await pilot.pause()
        assert (tmp_path / "photo.txt").exists()
        assert not renamed.exists()


async def test_narrow_layout(tmp_path: Path) -> None:
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.resize_terminal(80, 30)
        await pilot.pause()
        assert app.query_one("#body").has_class("-narrow")
        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        assert not app.query_one("#body").has_class("-narrow")


def test_search_keys_do_not_bind_command_or_shift_g() -> None:
    keys = [binding.key for binding in RenamerApp.BINDINGS]
    assert "slash" in keys
    assert "ctrl+g" in keys
    assert "right_square_bracket" in keys
    assert "left_square_bracket" in keys
    assert "P" in keys
    assert "super+g" not in keys
    assert "cmd+g" not in keys
    assert "ctrl+shift+g" not in keys


async def _until(pilot, app: RenamerApp, needle: str) -> None:
    for _ in range(40):
        await pilot.pause(0.05)
        caption = str(app.query_one("#preview-caption").render())
        body = str(app.query_one("#preview-fallback").render())
        if needle in caption or needle in body:
            return
    raise AssertionError(needle)


async def _until_picture(pilot, app: RenamerApp, size: tuple[int, int]):
    """Wait until the id lookup finds a mounted stand-in of this size.

    A CSS query can still see a picture whose mount was rejected for a
    duplicate id. ``query_one`` uses the id map, so that failure stays visible.
    """
    last = "no picture"
    for _ in range(40):
        await pilot.pause(0.05)
        try:
            picture = app.query_one("#preview-image")
        except Exception as exc:
            last = type(exc).__name__
            continue
        image = getattr(picture, "image", None)
        if getattr(picture, "mounted_ok", False) and image is not None and image.size == size:
            return picture
        last = f"mounted={getattr(picture, 'mounted_ok', None)} size={getattr(image, 'size', None)}"
    raise AssertionError(last)


async def test_find_escapes_without_changing_selection(tmp_path: Path) -> None:
    for name in ("catalog.txt", "cat.txt", "dog.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("v")
        selected = set(app.selected)
        await pilot.press("G")
        await pilot.press("/")
        await pilot.press("c", "a", "t")
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.cursor_row == 0
        assert "find 1/2" in app.sub_title
        assert app.selected == selected
        assert app.visual is True
        cell = table.get_cell_at(Coordinate(0, 4))
        assert isinstance(cell, Text)
        assert any("reverse" in str(span.style) for span in cell.spans)
        await pilot.press("escape")
        assert table.cursor_row == 2
        assert app.selected == selected
        assert app._search_active is False


async def test_enter_arms_search_and_brackets_still_jump_changes(tmp_path: Path) -> None:
    (tmp_path / "2020-01-02-030405-photo.jpg").write_bytes(b"jpeg")
    (tmp_path / "plain.txt").write_text("plain", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+g")
        assert "No search." in str(app.query_one("#status").render())
        await pilot.press("/")
        await pilot.press("p", "l", "a", "i", "n")
        await pilot.press("enter")
        table = app.query_one(DataTable)
        assert table.cursor_row == 1
        assert app._search_active is True
        assert len(app.screen_stack) == 1
        assert (tmp_path / "2020-01-02-030405-photo.jpg").exists()
        await pilot.press("n")
        assert "No other match." in str(app.query_one("#status").render())
        await pilot.press("]")
        assert table.cursor_row == 0
        await pilot.press("/")
        await pilot.press("backspace")
        assert app.query_one("#find", Input).value == ""
        await pilot.press("enter")
        assert app._search_active is False
        await pilot.press("n")
        assert "No other file will change." in str(app.query_one("#status").render())


async def test_ctrl_g_follows_an_accepted_search(tmp_path: Path) -> None:
    for name in ("catalog.txt", "cat.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("c", "a", "t")
        await pilot.press("enter")
        assert app.query_one(DataTable).cursor_row == 0
        await pilot.press("ctrl+g")
        assert app.query_one(DataTable).cursor_row == 1


async def test_preview_text_and_toggle(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Hello preview\nsecond\n", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, app, "Hello preview")
        assert not app.query_one("#preview-slot").has_class("-off")
        await pilot.press("P")
        assert app.query_one("#preview-slot").has_class("-off")
        await pilot.press("P")
        assert not app.query_one("#preview-slot").has_class("-off")
        await _until(pilot, app, "Hello preview")


async def test_preview_renders_each_picture_in_a_row(tmp_path: Path) -> None:
    from PIL import Image

    for index, color in enumerate(((10, 0, 0), (0, 10, 0), (0, 0, 10), (10, 10, 0))):
        Image.new("RGB", (8 + index, 6), color).save(tmp_path / f"pic{index}.png")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        for index in range(4):
            if index:
                await pilot.press("j")
            await _until(pilot, app, f"pic{index}.png")
            picture = await _until_picture(pilot, app, (8 + index, 6))
            assert picture.mounted_ok
            assert picture.image is not None
            assert picture.image.size == (8 + index, 6)
            fallback = app.query_one("#preview-fallback", Static)
            assert fallback.display is False
            assert "Picture unavailable" not in str(fallback.render())


async def test_preview_falls_back_when_the_widget_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.preview.load_image_widget", lambda: None)
    from PIL import Image

    Image.new("RGB", (8, 8), (9, 9, 9)).save(tmp_path / "dot.png")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, app, "8x8")
        assert not app.query("#preview-image")
        detail = str(app.query_one("#detail-body").render())
        assert "dot.png" in detail


async def test_video_source_letter_is_quicktime(tmp_path: Path) -> None:
    from tests.test_quicktime import CAPTURE, movie, mvhd

    (tmp_path / "clip.mp4").write_bytes(movie(mvhd(CAPTURE)))
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.get_cell_at(Coordinate(0, 1)).plain == "ok"
        assert "e" not in table.get_cell_at(Coordinate(0, 3)).plain
        detail = str(app.query_one("#detail-body").render())
        assert "Date source: QuickTime" in detail


async def test_at_jumps_to_the_previous_warning(tmp_path: Path) -> None:
    from PIL import Image

    from tui_renamer.metadata import write_image_exif_datetime

    (tmp_path / "a-note.txt").write_text("x", encoding="utf-8")
    for name in ("b-2020-06-01-120000-photo.jpg", "c-2020-06-01-120000-photo.jpg"):
        path = tmp_path / name
        Image.new("RGB", (8, 8), (20, 30, 40)).save(path, format="JPEG")
        write_image_exif_datetime(path, datetime(2020, 6, 1, 17, 0, tzinfo=timezone.utc))
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.rules.source_tz = "UTC"
        app._facts.clear()
        app.refresh_view()
        await pilot.pause()
        table = app.query_one(DataTable)
        await pilot.press("G")
        assert table.cursor_row == 2
        await pilot.press("@")
        assert table.cursor_row == 1
        await pilot.press("@")
        assert table.cursor_row == 2


async def test_preview_and_zoom_are_remembered(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.press("1")
        await pilot.pause()
        assert app.preview_on is False
        assert app._zoom == "files"

    again = RenamerApp(tmp_path)
    async with again.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert again.preview_on is False
        assert again.query_one("#preview-slot").has_class("-off")
        assert again._zoom == "files"
        assert again.screen.has_class("-zoom-files")
        await pilot.press("ctrl+0")
        await pilot.pause()
        assert again.preview_on is True
        assert again._zoom is None
        assert not again.query_one("#preview-slot").has_class("-off")
        assert not again.screen.has_class("-zoom-files")

    reset = RenamerApp(tmp_path)
    async with reset.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert reset.preview_on is True
        assert reset._zoom is None


def _chip_labels(review) -> list[str]:
    return [str(button.label) for button in review.query(".scan-chip")]


def _jpeg_photo(path: Path, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    from PIL import Image

    Image.new("RGB", (16, 12), color).save(path, format="JPEG")


async def test_scan_review_is_off_at_startup_then_accepts(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, ReviewScreen)
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        review = app.screen
        assert review.related is None
        assert review.query_one("#scan-help").display is False
        focused = review.focused
        assert getattr(focused, "id", None) != "scan-model"
        review.query_one("#scan-model", Input).value = "mock"
        review.query_one("#scan-model", Input).focus()
        await pilot.press("enter")
        await review.workers.wait_for_complete()
        await pilot.pause()
        labels = _chip_labels(review)
        assert any("Aunt May" in label for label in labels)
        assert any("picnic" in label for label in labels)
        list(review.query(".scan-chip"))[0].press()
        await pilot.pause()
        first = list(review.query(".scan-chip"))[0]
        assert "name" in str(first.label)
        assert "written" in review.bits[0].tooltip()
        assert review.bits[0].use_meta is False
        review.action_close()
        await pilot.pause()
        assert not isinstance(app.screen, ReviewScreen)
        assert not notes_path_for(photo).exists()
        preview = app.preview_for(0)
        assert "Aunt" in preview or "June" in preview or "1952" in preview


async def test_scan_review_escape_does_not_write(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo, (1, 1, 1))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ReviewScreen)
        assert not notes_path_for(photo).exists()


async def test_scan_review_cancels_a_read(tmp_path: Path) -> None:
    import threading

    from tui_renamer.scan_backend import BackendError, Recognition
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    class HangBackend:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def recognize(self, image, *, model: str) -> Recognition:
            del image, model
            self.started.set()
            self.release.wait(timeout=5)
            raise BackendError("late")

        def list_vision_models(self) -> list[str]:
            return []

        def list_loaded_models(self) -> list[str]:
            return []

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo, (3, 3, 3))
    backend = HangBackend()
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        app.screen.query_one("#scan-model", Input).value = "mock"
        app.screen.action_read()
        await pilot.pause()
        assert backend.started.wait(timeout=2)
        app.screen.action_cancel_read()
        await pilot.pause()
        status = str(app.screen.query_one("#scan-status").render())
        assert "cancelled" in status.lower()
        await pilot.press("escape")
        await pilot.pause()
        backend.release.set()
        assert not notes_path_for(photo).exists()


async def test_scan_review_compact_status_help_installed_and_manual(
    tmp_path: Path,
) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import load_scan_settings
    from tui_renamer.scan_dates import ScanDate
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo, (5, 5, 5))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend(
        discover_error="Ollama is not reachable. Start it yourself with: ollama serve",
        dates=[ScanDate("year", "1952", 1952)],
    )
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        models = str(review.query_one("#scan-models").render())
        assert "ollama serve" in models.casefold() or "not reachable" in models.casefold()
        assert review.query_one("#scan-help").display is False
        review.action_help()
        help_text = str(review.query_one("#scan-help").render())
        assert "optional" in help_text.casefold()
        box = review.query_one("#scan-model", Input)
        box.value = "typed-vision"
        box.focus()
        await pilot.press("enter")
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert load_scan_settings().model == "typed-vision"
        add = review.query_one("#scan-add", Input)
        add.value = "1952"
        add.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert any("1952" in item.label for item in review.bits)
        review.action_close()
        await pilot.pause()
        assert not notes_path_for(photo).exists()
        session = app.scan_sessions[str(photo)]
        kinds = [item.date.kind for item in session.bits if item.date is not None]
        assert "year" in kinds


async def test_scan_review_installed_loaded_and_related_browse(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    extra = tmp_path / "zz.jpg"
    _jpeg_photo(front, (7, 7, 7))
    _jpeg_photo(back, (8, 8, 8))
    _jpeg_photo(extra, (9, 9, 9))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend(
        vision_models=["gemma3:4b", "qwen3.5:2b"],
        loaded_models=["gemma3:4b"],
    )
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        shown = str(review.query_one("#scan-models").render())
        assert "gemma3:4b*" in shown or "gemma3:4b" in shown
        picker = review.query_one("#scan-model-pick", OptionList)
        assert picker.display is True
        prompts = []
        for index in range(picker.option_count):
            prompt = picker.get_option_at_index(index).prompt
            prompts.append(prompt.plain if hasattr(prompt, "plain") else str(prompt))
        blob = " ".join(prompts)
        assert "gemma3:4b" in blob
        assert "loaded" in blob.casefold()
        related = review.query_one("#scan-related-list", OptionList)
        prompts = []
        for index in range(related.option_count):
            prompt = related.get_option_at_index(index).prompt
            prompts.append(prompt.plain if hasattr(prompt, "plain") else str(prompt))
        blob = " ".join(prompts)
        assert "Likely" in blob
        assert "trip_b.jpg" in blob
        assert "Unknown" in blob or "Unlikely" in blob or "Possible" in blob
        assert "zz.jpg" in blob
        assert app.scan_backend.recognize_calls == []


async def test_scan_review_remote_host_needs_a_second_read(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import ScanSettings, save_scan_settings
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo, (7, 7, 7))
    save_scan_settings(ScanSettings(host="http://example.test:11434", model="mock"))
    backend = MockBackend(vision_models=["mock"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert backend.recognize_calls == []
        status = str(review.query_one("#scan-status").render())
        assert "example.test" in status
        review.action_read()
        status = str(review.query_one("#scan-status").render())
        assert "not this computer" in status
        assert backend.recognize_calls == []
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert len(backend.recognize_calls) == 1
        assert any("Aunt May" in label for label in _chip_labels(review))


async def test_scan_review_stage_apply_overwrite_skip_and_undo(tmp_path: Path) -> None:
    import piexif

    from tui_renamer.metadata import read_exif, write_image_exif_datetime
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_dates import ScanDate
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    _jpeg_photo(photo, (11, 12, 13))
    write_image_exif_datetime(photo, datetime(2020, 6, 1, 17, 0, tzinfo=timezone.utc))
    before = read_exif(photo).naive
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend(
        dates=[ScanDate("day", "1961-07-04", 1961, 7, 4, origin="written")],
        tokens=["lake"],
    )
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        for bit in review.bits:
            if bit.kind in {"date", "token", "text"}:
                if not bit.use_name:
                    review._toggle_name(bit)
                if bit.can_meta() and not bit.use_meta:
                    review._toggle_meta(bit)
        review.action_close()
        await pilot.pause()
        assert not notes_path_for(photo).exists()
        assert read_exif(photo).naive == before
        await pilot.press("enter")
        question = str(app.screen.query_one("#confirm-question").render())
        assert "skip" in question.casefold()
        await pilot.press("y")
        await pilot.pause()
        current = photo if photo.exists() else next(tmp_path.glob("keep-*.jpg"))
        assert read_exif(current).naive == before
        loaded = piexif.load(str(current))
        desc = loaded["0th"].get(piexif.ImageIFD.ImageDescription, b"")
        assert b"lake" in desc or b"Aunt" in desc or b"June" in desc or desc
        await pilot.press("u")
        await pilot.pause()
        assert read_exif(photo).naive == before


async def test_scan_review_apply_writes_written_day_and_description(
    tmp_path: Path,
) -> None:
    import piexif

    from tui_renamer.metadata import read_exif
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_dates import ScanDate
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "plain.jpg"
    _jpeg_photo(photo, (20, 21, 22))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend(
        transcription="",
        dates=[ScanDate("day", "1961-07-04", 1961, 7, 4, origin="written")],
        tokens=["picnic"],
        people_present=True,
        people_count=1,
    )
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        people = next(item for item in review.bits if item.kind == "people")
        assert people.use_name is False
        for bit in review.bits:
            if bit.kind in {"date", "token"}:
                bit.use_name = True
                bit.use_meta = True
        review._paint_chips()
        review.action_close()
        await pilot.pause()
        assert not notes_path_for(photo).exists()
        assert read_exif(photo).naive is None
        assert app.scan_pending
        item = next(iter(app.scan_pending.values()))
        assert item.moment is not None
        assert item.description == "picnic"
        await pilot.press("enter")
        question = str(app.screen.query_one("#confirm-question").render())
        assert "capture" in question.casefold()
        await pilot.press("y")
        await pilot.pause()
        renamed = sorted(tmp_path.glob("plain-*.jpg"))
        assert renamed
        assert notes_path_for(renamed[0]).exists()
        assert "Wrote" in app._message
        clock = read_exif(renamed[0])
        assert clock.naive is not None
        assert clock.naive.date().isoformat() == "1961-07-04"
        assert clock.naive.hour == 12
        loaded = piexif.load(str(renamed[0]))
        assert loaded["0th"][piexif.ImageIFD.ImageDescription] == b"picnic"
        await pilot.press("u")
        await pilot.pause()
        assert photo.exists()
        assert read_exif(photo).naive is None


def _option_blob(picker: OptionList) -> str:
    prompts = []
    for index in range(picker.option_count):
        prompt = picker.get_option_at_index(index).prompt
        prompts.append(prompt.plain if hasattr(prompt, "plain") else str(prompt))
    return " ".join(prompts)


async def test_scan_first_use_dropdown_saves_and_reads_once(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import load_scan_settings
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    backend = MockBackend(vision_models=["gemma3:4b"], loaded_models=["gemma3:4b"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert getattr(review.focused, "id", None) != "scan-model"
        picker = review.query_one("#scan-model-pick", OptionList)
        assert picker.display is True
        assert backend.recognize_calls == []
        picker.highlighted = 0
        picker.action_select()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert load_scan_settings().model == "gemma3:4b"
        assert len(backend.recognize_calls) == 1
        assert backend.recognize_calls[0][1] == "gemma3:4b"
        await pilot.pause()
        assert len(backend.recognize_calls) == 1
        assert any("Aunt May" in label for label in _chip_labels(review))


async def test_scan_saved_model_auto_reads_current_image_once(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import ScanSettings, save_scan_settings
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    extra = tmp_path / "zz.jpg"
    _jpeg_photo(photo, (1, 2, 3))
    _jpeg_photo(extra, (4, 5, 6))
    save_scan_settings(ScanSettings(model="gemma3:4b"))
    backend = MockBackend(vision_models=["gemma3:4b"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert len(backend.recognize_calls) == 1
        assert backend.recognize_calls[0][0].endswith("solo.jpg")
        related = review.query_one("#scan-related-list", OptionList)
        assert "zz.jpg" in _option_blob(related)
        assert all(not call[0].endswith("zz.jpg") for call in backend.recognize_calls)
        await pilot.pause()
        assert len(backend.recognize_calls) == 1


async def test_scan_unavailable_model_falls_back_to_chooser(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import ScanSettings, save_scan_settings
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    save_scan_settings(ScanSettings(model="missing-vision"))
    backend = MockBackend(vision_models=["gemma3:4b"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert backend.recognize_calls == []
        status = str(review.query_one("#scan-status").render())
        assert "not installed" in status.casefold()
        assert review.query_one("#scan-model-pick", OptionList).display is True
        add = review.query_one("#scan-add", Input)
        add.value = "Aunt May"
        add.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert any("Aunt May" in item.label for item in review.bits)


async def test_scan_stale_read_is_discarded_on_close(tmp_path: Path) -> None:
    import threading

    from tui_renamer.scan_backend import BackendError, Recognition
    from tui_renamer.scan_config import ScanSettings, save_scan_settings
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    class HangBackend:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.recognize_calls: list[tuple[str, str]] = []

        def recognize(self, image, *, model: str) -> Recognition:
            self.recognize_calls.append((str(image), model))
            self.started.set()
            self.release.wait(timeout=5)
            raise BackendError("late")

        def list_vision_models(self) -> list[str]:
            return ["mock"]

        def list_loaded_models(self) -> list[str]:
            return ["mock"]

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo, (9, 9, 9))
    save_scan_settings(ScanSettings(model="mock"))
    backend = HangBackend()
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert backend.started.wait(timeout=2)
        await pilot.press("escape")
        await pilot.pause()
        backend.release.set()
        await pilot.pause()
        assert not isinstance(app.screen, ReviewScreen)
        assert not notes_path_for(photo).exists()


async def test_scan_narrow_viewport_keeps_actions(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        assert review.has_class("-narrow")
        assert review.query_one("#scan-left").display
        assert review.query_one("#scan-right").display
        assert review.query_one("#scan-orig-slot").display
        assert review.query_one("#scan-rel-slot").display
        assert review.query_one("#scan-close").display
        assert review.query_one("#scan-actions").display
        assert review.query_one("#scan-read").display
        assert review.query_one("#scan-read-related").display


async def test_scan_highlight_does_not_switch_original_read(tmp_path: Path) -> None:
    from tui_renamer.preview import build_preview
    from tui_renamer.scan_backend import MockBackend, Recognition
    from tui_renamer.scan_ui import ReviewScreen

    class SplitBackend(MockBackend):
        def recognize(self, image, *, model: str) -> Recognition:
            if Path(image).name == "trip_b.jpg":
                self.recognize_calls.append((str(image), model))
                return Recognition(
                    transcription="verso writing",
                    notes="",
                    dates=[],
                    engine="mock",
                    model=model or "mock",
                    raw="verso writing",
                    tokens=["verso"],
                    people_present=False,
                    people_count=None,
                )
            return super().recognize(image, model=model)

    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    _jpeg_photo(front, (1, 2, 3))
    _jpeg_photo(back, (4, 5, 6))
    backend = SplitBackend(vision_models=["mock"], loaded_models=["mock"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        original_count = len(backend.recognize_calls)
        assert backend.recognize_calls[-1][0].endswith("trip_a.jpg")
        assert any("Aunt May" in label for label in _chip_labels(review))
        assert review.related is None
        review.highlight = back
        review._preview_job += 1
        review._fill_related_preview(back, review._preview_job)
        await review.workers.wait_for_complete()
        await pilot.pause()
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert len(backend.recognize_calls) == original_count + 1
        assert backend.recognize_calls[-1][0].endswith("trip_a.jpg")
        assert review.related is None
        review.action_read_related()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert backend.recognize_calls[-1][0].endswith("trip_b.jpg")
        assert review.related == back
        sources = {item.source for item in review.bits}
        assert "trip_a.jpg" in sources
        assert "trip_b.jpg" in sources
        assert any("verso" in item.label for item in review.bits)
        assert any("Aunt May" in item.label for item in review.bits)
        orig = review.query_one("#scan-orig-slot")
        rel = review.query_one("#scan-rel-slot")
        assert orig.children
        assert rel.children
        stale_job = review._preview_job
        review._preview_job += 1
        review._related_preview_done(stale_job, back, build_preview(back, review.cache))
        assert review.query_one("#scan-orig-slot").children


async def test_scan_suffix_preview_accept_reopen_refresh_apply(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_notes import load_notes, notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    other = tmp_path / "plain.jpg"
    _jpeg_photo(photo, (8, 8, 8))
    _jpeg_photo(other, (9, 9, 9))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "alpha"
        add.focus()
        await pilot.press("enter")
        add.value = "beta"
        await pilot.press("enter")
        await pilot.pause()
        alpha = next(item for item in review.bits if item.label == "alpha")
        beta = next(item for item in review.bits if item.label == "beta")
        review._toggle_name(beta)
        review._toggle_name(alpha)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-beta-alpha.jpg" in shown
        while beta.use_name:
            review._toggle_name(beta)
        review._toggle_name(beta)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-alpha-beta.jpg" in shown
        alpha.label = "alpha-edit"
        review._paint_name_preview()
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-alpha-edit-beta.jpg" in shown
        review.action_close()
        await pilot.pause()
        assert not notes_path_for(photo).exists()
        assert app.scan_sessions[str(photo)].suffix_parts == ["alpha-edit", "beta"]
        assert app.preview_for(0) == "keep-alpha-edit-beta.jpg"
        app.rules.strip_original_name = True
        assert app.preview_for(0) == "alpha-edit-beta.jpg"
        app.rules.strip_original_name = False
        app.manual[0] = "custom.jpg"
        assert app.preview_for(0) == "custom-alpha-edit-beta.jpg"
        del app.manual[0]
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "gamma"
        add.focus()
        await pilot.press("enter")
        review.action_close()
        await pilot.pause()
        assert not notes_path_for(photo).exists()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        labels = [item.label for item in review.bits]
        assert "gamma" in labels
        assert "alpha-edit" in labels
        assert "beta" in labels
        review.action_close()
        await pilot.pause()
        app.action_reload_files()
        await pilot.pause()
        assert app.preview_for(0) == "keep-alpha-edit-beta.jpg"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        renamed = tmp_path / "keep-alpha-edit-beta.jpg"
        assert renamed.exists()
        assert notes_path_for(renamed).exists()
        assert not notes_path_for(photo).exists()
        loaded = load_notes(notes_path_for(renamed))
        assert loaded is not None
        assert loaded.name_suffix == ["alpha-edit", "beta"]
        await pilot.press("u")
        await pilot.pause()
        assert photo.exists()
        assert notes_path_for(photo).exists()
        other_index = next(
            index for index, path in enumerate(app.files) if path.name == "plain.jpg"
        )
        assert app.preview_for(other_index) == "plain.jpg"
        assert other.exists()


async def test_scan_suffix_format_toggles_keep_raw_labels(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_notes import load_notes, notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    other = tmp_path / "plain.jpg"
    _jpeg_photo(photo, (8, 8, 8))
    _jpeg_photo(other, (9, 9, 9))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "Aunt May!"
        add.focus()
        await pilot.press("enter")
        add.value = "Hello.World"
        await pilot.press("enter")
        aunt = next(item for item in review.bits if item.label == "Aunt May!")
        hello = next(item for item in review.bits if item.label == "Hello.World")
        review._toggle_name(aunt)
        review._toggle_name(hello)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-Aunt May!-Hello.World.jpg" in shown
        review.action_close()
        await pilot.pause()
        assert app.scan_suffix[str(photo)] == ["Aunt May!", "Hello.World"]
        assert app.preview_for(0) == "keep-Aunt May!-Hello.World.jpg"
        app.rules.remove_caps = True
        assert app.preview_for(0) == "keep-aunt may!-hello.world.jpg"
        app.rules.remove_caps = False
        app.rules.remove_special = True
        assert app.preview_for(0) == "keep-Aunt May-Hello.World.jpg"
        app.rules.remove_special = False
        app.rules.replace_spaces = True
        assert app.preview_for(0) == "keep-Aunt-May!-Hello.World.jpg"
        app.rules.replace_spaces = False
        app.rules.replace_dots = True
        assert app.preview_for(0) == "keep-Aunt May!-Hello-World.jpg"
        app.rules.replace_dots = False
        app.rules.remove_caps = True
        app.rules.remove_special = True
        app.rules.replace_spaces = True
        app.rules.replace_dots = True
        assert app.preview_for(0) == "keep-aunt-may-hello-world.jpg"
        app.rules.datetime_stamp = "2020-01-01-000000"
        app.rules.add_datetime = True
        assert app.preview_for(0) == "2020-01-01-000000-keep-aunt-may-hello-world.jpg"
        app.rules.add_datetime = False
        other_index = next(
            index for index, path in enumerate(app.files) if path.name == "plain.jpg"
        )
        assert app.preview_for(other_index) == "plain.jpg"
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        labels = [item.label for item in review.bits]
        assert labels.count("Aunt May!") == 1
        assert labels.count("Hello.World") == 1
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-aunt-may-hello-world.jpg" in shown
        add = review.query_one("#scan-add", Input)
        add.value = "///"
        add.focus()
        await pilot.press("enter")
        slash = next(item for item in review.bits if item.label == "///")
        review._toggle_name(slash)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-aunt-may-hello-world.jpg" in shown
        review.action_close()
        await pilot.pause()
        assert app.scan_suffix[str(photo)] == ["Aunt May!", "Hello.World", "///"]
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        renamed = tmp_path / "keep-aunt-may-hello-world.jpg"
        assert renamed.exists()
        loaded = load_notes(notes_path_for(renamed))
        assert loaded is not None
        assert loaded.name_suffix == ["aunt-may", "hello-world"]
        assert loaded.raw_transcription == ""
        assert [item.label for item in loaded.bits if item.label == "Aunt May!"]
        assert [item.label for item in loaded.bits if item.label == "Hello.World"]
        assert app.preview_for(0) == "keep-aunt-may-hello-world.jpg"
        assert app._rename_pairs() == []
        app.rules.replace_spaces = False
        assert app.preview_for(0) == "keep-aunt may-hello-world.jpg"
        app.rules.replace_spaces = True
        assert app.preview_for(0) == "keep-aunt-may-hello-world.jpg"
        await pilot.press("u")
        await pilot.pause()
        assert photo.exists()
        assert app.scan_suffix[str(photo)] == ["Aunt May!", "Hello.World", "///"]
        assert app.preview_for(0) == "keep-aunt-may-hello-world.jpg"
        assert other.exists()
        assert app.preview_for(other_index) == "plain.jpg"


async def test_scan_written_day_filename_clock_apply_reopen(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_bits import Bit
    from tui_renamer.scan_dates import ScanDate
    from tui_renamer.scan_notes import load_notes, notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    other = tmp_path / "plain.jpg"
    _jpeg_photo(photo, (8, 8, 8))
    _jpeg_photo(other, (9, 9, 9))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        day = ScanDate("day", "Oct 20, 1994", 1994, 10, 20, origin="written")
        item = Bit(
            kind="date",
            label="Oct 20, 1994",
            source=photo.name,
            model="",
            origin="written",
            date=day,
            manual=True,
        )
        review.bits.append(item)
        review._toggle_name(item)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-1994-10-20-120000.jpg" in shown
        assert item.label == "Oct 20, 1994"
        assert "12:00" in item.tooltip()
        review.action_close()
        await pilot.pause()
        assert app.scan_suffix[str(photo)] == ["Oct 20, 1994"]
        assert app.preview_for(0) == "keep-1994-10-20-120000.jpg"
        app.rules.remove_caps = True
        app.rules.remove_special = True
        app.rules.replace_spaces = True
        app.rules.replace_dots = True
        assert app.preview_for(0) == "keep-1994-10-20-120000.jpg"
        other_index = next(
            index for index, path in enumerate(app.files) if path.name == "plain.jpg"
        )
        assert app.preview_for(other_index) == "plain.jpg"
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        labels = [chip.label for chip in review.bits]
        assert "Oct 20, 1994" in labels
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: keep-1994-10-20-120000.jpg" in shown
        review.action_close()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        renamed = tmp_path / "keep-1994-10-20-120000.jpg"
        assert renamed.exists()
        loaded = load_notes(notes_path_for(renamed))
        assert loaded is not None
        assert loaded.name_suffix == ["1994-10-20-120000"]
        assert loaded.raw_transcription == ""
        assert any(chip.label == "Oct 20, 1994" for chip in loaded.bits)
        assert app.preview_for(0) == "keep-1994-10-20-120000.jpg"
        assert app._rename_pairs() == []
        await pilot.press("u")
        await pilot.pause()
        assert photo.exists()
        assert app.scan_suffix[str(photo)] == ["Oct 20, 1994"]
        assert app.preview_for(0) == "keep-1994-10-20-120000.jpg"
        assert other.exists()
        assert app.preview_for(other_index) == "plain.jpg"


async def test_scan_english_date_token_typed_and_notes_preview(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_bits import Bit
    from tui_renamer.scan_notes import ScanNotes, notes_path_for, save_notes
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "2026-11-05-13-44-0041.tiff"
    other = tmp_path / "plain.jpg"
    _jpeg_photo(photo, (8, 8, 8))
    _jpeg_photo(other, (9, 9, 9))
    expected = "2026-11-05-13-44-0041-1994-10-20-120000.tiff"
    backend = MockBackend(
        transcription="Aunt May picnic Oct 20, 1994",
        dates=[],
        tokens=["Oct 20, 1994"],
        people_present=None,
        people_count=None,
    )
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        token = next(item for item in review.bits if item.label == "Oct 20, 1994")
        assert token.kind == "token"
        review._toggle_name(token)
        shown = str(review.query_one("#scan-name-preview").render())
        assert f"New: {expected}" in shown
        assert token.label == "Oct 20, 1994"
        assert token.use_meta is False
        sentence = next(
            item for item in review.bits if "picnic" in item.label and item.kind == "text"
        )
        review._toggle_name(sentence)
        shown = str(review.query_one("#scan-name-preview").render())
        assert "Oct 20, 1994" in shown
        assert "1994-10-20-120000" in shown
        while sentence.use_name:
            review._toggle_name(sentence)
        review.action_close()
        await pilot.pause()
        assert app.preview_for(0) == expected
        assert str(photo) not in app.scan_pending
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "Oct 20, 1994"
        add.focus()
        await pilot.press("enter")
        typed = next(
            item
            for item in review.bits
            if item.manual and item.label == "Oct 20, 1994" and item.kind == "date"
        )
        assert typed.use_meta is False
        for item in list(review.bits):
            if item is not typed and item.use_name:
                review._toggle_name(item)
        review._toggle_name(typed)
        shown = str(review.query_one("#scan-name-preview").render())
        assert f"New: {expected}" in shown
        review.action_close()
        await pilot.pause()
        assert app.preview_for(0) == expected
        other_index = next(
            index for index, path in enumerate(app.files) if path.name == "plain.jpg"
        )
        assert app.preview_for(other_index) == "plain.jpg"

    notes_photo = tmp_path / "oldscan.jpg"
    _jpeg_photo(notes_photo, (3, 3, 3))
    save_notes(
        notes_path_for(notes_photo),
        ScanNotes(
            front=notes_photo.name,
            back=None,
            pairing_reason="none",
            pairing_confirmed=False,
            engine="mock",
            model="mock",
            raw_transcription="Oct 20, 1994 Aunt May",
            notes="",
            bits=[
                Bit(
                    kind="token",
                    label="Oct 20, 1994",
                    source=notes_photo.name,
                    model="mock",
                    origin="inferred",
                    use_name=True,
                    name_order=1,
                )
            ],
        ),
    )
    app2 = RenamerApp(tmp_path)
    app2.scan_backend = MockBackend(dates=[], tokens=[], transcription="")
    async with app2.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        index = next(i for i, path in enumerate(app2.files) if path.name == notes_photo.name)
        app2._move_to(index)
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app2.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        restored = next(item for item in review.bits if item.label == "Oct 20, 1994")
        assert restored.kind == "token"
        assert restored.date is None
        shown = str(review.query_one("#scan-name-preview").render())
        assert "New: oldscan-1994-10-20-120000.jpg" in shown
        assert restored.use_meta is False
        review.action_close()
        await pilot.pause()
        assert app2.preview_for(index) == "oldscan-1994-10-20-120000.jpg"
        assert notes_photo.name not in [item.path.name for item in app2.scan_pending.values()]


async def test_scan_lock_ignores_suffix(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "lockme.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        app.locked[0] = "lockme.jpg"
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "tag"
        add.focus()
        await pilot.press("enter")
        item = review.bits[0]
        review._toggle_name(item)
        shown = str(review.query_one("#scan-name-preview").render())
        assert shown.startswith("Locked:")
        review.action_close()
        await pilot.pause()
        assert app.preview_for(0) == "lockme.jpg"
        assert app._rename_pairs()[0][1] == "lockme.jpg"


async def test_scan_name_toggle_off_does_not_add_meta(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "picnic"
        add.focus()
        await pilot.press("enter")
        await pilot.pause()
        chip = list(review.query(".scan-chip"))[0]
        chip.press()
        await pilot.pause()
        assert review.bits[0].use_name is True
        assert review.bits[0].use_meta is False
        assert list(review.query(".scan-chip"))[0] is chip
        chip.press()
        await pilot.pause()
        assert review.bits[0].use_name is False
        assert review.bits[0].use_meta is False
        meta = list(review.query(".scan-meta"))[0]
        meta.press()
        await pilot.pause()
        assert review.bits[0].use_meta is True
        assert review.bits[0].use_name is False
        review.action_close()
        await pilot.pause()
        assert app.preview_for(0) == "solo.jpg"
        assert app.scan_pending


async def test_scan_first_candidate_preview_keeps_original_read(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    _jpeg_photo(front, (1, 1, 1))
    _jpeg_photo(back, (2, 2, 2))
    backend = MockBackend(vision_models=["mock"], loaded_models=["mock"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert review.related is None
        assert review.highlight == back
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert backend.recognize_calls[-1][0].endswith("trip_a.jpg")
        assert all(not call[0].endswith("trip_b.jpg") for call in backend.recognize_calls)


async def test_scan_metadata_only_apply_without_rename(tmp_path: Path) -> None:
    import piexif

    from tui_renamer.metadata import read_exif
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_dates import ScanDate
    from tui_renamer.scan_notes import notes_path_for
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    _jpeg_photo(photo, (3, 4, 5))
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend(
        transcription="",
        dates=[ScanDate("day", "1961-07-04", 1961, 7, 4, origin="written")],
        tokens=["lake"],
        people_present=False,
        people_count=None,
    )
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        review.query_one("#scan-model", Input).value = "mock"
        review.action_read()
        await review.workers.wait_for_complete()
        await pilot.pause()
        for bit in review.bits:
            if bit.can_meta() and not bit.use_meta:
                review._toggle_meta(bit)
            assert bit.use_name is False
        review.action_close()
        await pilot.pause()
        assert app.preview_for(0) == "keep.jpg"
        assert app.scan_pending
        assert not notes_path_for(photo).exists()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert photo.exists()
        clock = read_exif(photo)
        assert clock.naive is not None
        assert clock.naive.date().isoformat() == "1961-07-04"
        loaded = piexif.load(str(photo))
        assert b"lake" in loaded["0th"][piexif.ImageIFD.ImageDescription]
        assert notes_path_for(photo).exists()


async def test_scan_jk_moves_chips_without_toggle(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "alpha"
        add.focus()
        await pilot.press("enter")
        add.value = "beta"
        await pilot.press("enter")
        await pilot.pause()
        chips = list(review.query(".scan-chip"))
        chips[0].focus()
        await pilot.pause()
        review.action_cursor_down()
        await pilot.pause()
        assert review.focused is chips[1]
        assert review.bits[0].use_name is False
        assert review.bits[1].use_name is False
        review.action_cursor_up()
        await pilot.pause()
        assert review.focused is chips[0]


async def test_scan_jk_related_highlight_does_not_read(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    extra = tmp_path / "zz.jpg"
    _jpeg_photo(front, (1, 1, 1))
    _jpeg_photo(back, (2, 2, 2))
    _jpeg_photo(extra, (3, 3, 3))
    backend = MockBackend(vision_models=["mock"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        before = list(backend.recognize_calls)
        review.action_related()
        review.action_cursor_down()
        await pilot.pause()
        assert review.related is None
        assert backend.recognize_calls == before
        review.action_cursor_up()
        await pilot.pause()
        assert review.related is None
        assert backend.recognize_calls == before


async def test_scan_model_jk_does_not_read_until_enter(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import load_scan_settings
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    backend = MockBackend(vision_models=["gemma3:4b", "qwen3.5:2b"], loaded_models=["gemma3:4b"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        await review.workers.wait_for_complete()
        await pilot.pause()
        picker = review.query_one("#scan-model-pick", OptionList)
        assert picker.display is True
        picker.focus()
        review.action_cursor_down()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert backend.recognize_calls == []
        picker.action_select()
        await review.workers.wait_for_complete()
        await pilot.pause()
        assert load_scan_settings().model in {"gemma3:4b", "qwen3.5:2b"}
        assert len(backend.recognize_calls) == 1


async def test_scan_input_types_jkq_escape_blurs_tab_leaves(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.focus()
        await pilot.press("j")
        await pilot.press("k")
        await pilot.press("q")
        await pilot.pause()
        assert add.value == "jkq"
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert add.value == "jkq"
        assert not isinstance(review.focused, Input)
        add.focus()
        await pilot.press("tab")
        await pilot.pause()
        assert review.focused is not add


async def test_scan_q_closes_keeps_suffix_app_running(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_ui import ReviewScreen

    photo = tmp_path / "keep.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.scan_backend = MockBackend()
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        assert isinstance(review, ReviewScreen)
        add = review.query_one("#scan-add", Input)
        add.value = "lake"
        add.focus()
        await pilot.press("enter")
        await pilot.pause()
        review.query_one("#scan-left").focus()
        review._toggle_name(review.bits[0])
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, ReviewScreen)
        assert app.preview_for(0) == "keep-lake.jpg"
        assert app.is_running


async def test_main_jk_and_q_unchanged_outside_scan(tmp_path: Path) -> None:
    one = tmp_path / "a.txt"
    two = tmp_path / "b.txt"
    one.write_text("a", encoding="utf-8")
    two.write_text("b", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app._cursor == 0
        await pilot.press("j")
        await pilot.pause()
        assert app._cursor == 1
        await pilot.press("k")
        await pilot.pause()
        assert app._cursor == 0
        keys = [binding.key for binding in RenamerApp.BINDINGS]
        assert "q" in keys
        quit_action = next(binding.action for binding in RenamerApp.BINDINGS if binding.key == "q")
        assert quit_action == "quit"


async def test_edit_name_stages_on_enter_and_escape_cancels(tmp_path: Path) -> None:
    from tui_renamer.app import InsertScreen

    source = tmp_path / "original.txt"
    source.write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        assert isinstance(app.screen, InsertScreen)
        app.screen.query_one("#stem", Input).value = "staged"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app.screen_stack[0]
        assert app.manual[0] == "staged.txt"
        assert source.read_text(encoding="utf-8") == "keep"
        assert not (tmp_path / "staged.txt").exists()
        await pilot.press("i")
        app.screen.query_one("#stem", Input).value = "discarded"
        await pilot.press("escape")
        await pilot.pause()
        assert app.manual[0] == "staged.txt"
        assert source.exists()


async def test_refresh_cancel_preserves_staged_work(tmp_path: Path) -> None:
    from tui_renamer.app import ConfirmScreen

    (tmp_path / "original.txt").write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.selected.add(0)
        app.manual[0] = "staged.txt"
        app.locked[0] = "locked.txt"
        await pilot.press("r")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.selected == {0}
        assert app.manual == {0: "staged.txt"}
        assert app.locked == {0: "locked.txt"}
        await pilot.press("r", "y")
        await pilot.pause()
        assert not app.selected
        assert not app.manual
        assert not app.locked
        assert (tmp_path / "original.txt").exists()


async def test_layout_prioritizes_files_and_review_actions_fit(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend

    photo = tmp_path / "solo.jpg"
    _jpeg_photo(photo)
    app = RenamerApp(tmp_path)
    app.exiftool = None
    app.setfile = None
    app.scan_backend = MockBackend()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        assert app.query_one("#files").size.width > app.query_one("#detail").size.width
        await pilot.resize_terminal(60, 24)
        await pilot.pause()
        assert app.screen.has_class("-compact")
        assert app.query_one("#files").size.height >= 2
        assert app.query_one("#detail").display is False
        await pilot.press("S")
        await pilot.pause()
        review = app.screen
        dialog = review.query_one("#scan-dialog").content_region
        for ident in (
            "scan-close",
            "scan-read",
            "scan-read-related",
            "scan-name-preview",
            "scan-save-hint",
        ):
            widget = review.query_one(f"#{ident}")
            assert widget.region.height > 0
            assert dialog.contains_region(widget.region)


async def test_focused_option_enter_toggles_without_apply(tmp_path: Path) -> None:
    source = tmp_path / "PHOTO.txt"
    source.write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.selected.add(0)
        app.query_one("#group-cleanup", Collapsible).collapsed = False
        await pilot.pause()
        app.query_one("#opt-remove_caps").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.rules.remove_caps
        assert app.screen is app.screen_stack[0]
        assert source.exists()
        await pilot.press("space")
        await pilot.pause()
        assert not app.rules.remove_caps
