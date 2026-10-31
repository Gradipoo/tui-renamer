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
from textual.widgets import DataTable, Input, Static

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


async def test_select_and_visual_keep_scroll_and_order(tmp_path: Path) -> None:
    for index in range(220):
        (tmp_path / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=120)
        await pilot.pause()
        assert table.cursor_row == 120
        assert table.scroll_y > 0
        order = _row_keys(table)
        scroll = table.scroll_y
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
        table.move_cursor(row=120)
        await pilot.pause()
        order = _row_keys(table)
        scroll = table.scroll_y
        assert scroll > 0
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


async def test_options_strip_shows_each_toggle_on_one_line(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        options = app.query_one("#options")
        inner = options.content_region
        on = app.query_one("#opt-add_datetime", OptionToggle)
        off = app.query_one("#opt-add_modtime", OptionToggle)
        assert on.has_class("-on")
        assert not off.has_class("-on")
        assert _background(on) != _background(off)
        for toggle in app.query(OptionToggle):
            assert toggle.size.height == 1
            assert inner.contains_region(toggle.region)
            chip = toggle.render()
            assert chip.plain == f" {toggle.key_name} {toggle.caption} "
            assert chip.plain[1 + len(toggle.key_name)] == " "
            assert f"{toggle.key_name} {toggle.caption}" in _painted(toggle)
            assert toggle.tooltip
            key_spans = [
                span
                for span in chip.spans
                if span.start == 1 and span.end == 1 + len(toggle.key_name)
            ]
            assert key_spans
            style = str(key_spans[0].style)
            assert "underline" in style and "bold" in style
        for button in app.query("#opt-actions Button"):
            assert button.size.height == 1
            assert inner.contains_region(button.region)
            assert str(button.label) in _painted(button)
            assert button.tooltip
        idle = _background(off)
        assert await pilot.hover("#opt-add_modtime")
        await pilot.pause()
        assert _background(off) != idle
        off.focus()
        await pilot.pause()
        assert _background(off) != idle
        assert app.query_one("#opt-toggles").size.height == 1
        await pilot.resize_terminal(72, 40)
        await pilot.pause()
        options = app.query_one("#options")
        inner = options.content_region
        assert app.query_one("#opt-toggles").size.height >= 2
        for toggle in app.query(OptionToggle):
            assert toggle.size.height == 1
            assert inner.contains_region(toggle.region)
            assert toggle.render().plain == f" {toggle.key_name} {toggle.caption} "
            assert f"{toggle.key_name} {toggle.caption}" in _painted(toggle)
        for button in app.query("#opt-actions Button"):
            assert button.size.height == 1
            assert inner.contains_region(button.region)


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
        assert "1 of 2 will change" in app.sub_title
        table = app.query_one(DataTable)
        labels = [column.label.plain for column in table.columns.values()]
        assert labels[:4] == ["Sel", "Src", "Flag", "Miss"]
        assert labels[-1] == "Stat"
        widths = {column.label.plain: column.width for column in table.columns.values()}
        assert widths["Src"] == 3
        assert widths["Flag"] == 4
        assert widths["Miss"] == 4
        assert widths["Stat"] == 4
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
        assert table.get_cell_at(Coordinate(0, 1)).plain == "E"
        assert table.get_cell_at(Coordinate(0, 2)).plain == "H"
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("!")
        assert table.cursor_row == 0
        detail = str(app.query_one("#detail-body").render())
        assert "Src EXIF" in detail
        assert "Flag looks like a timezone offset" in detail
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
        assert app.query_one("#body").has_class("-narrow")
        await pilot.resize_terminal(120, 40)
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
