"""Pilot tests for panes, the palette, and one rename plus undo."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.command import CommandPalette
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input

from tui_renamer.app import RenamerApp, RulesPane, linux_stdin_deleted


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


async def test_number_keys_focus_panes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        assert app.focused is not None
        assert app.focused.id == "detail"
        await pilot.press("3")
        assert app.focused is not None
        assert app.focused.id == "rules"
        await pilot.press("1")
        assert isinstance(app.focused, DataTable)


async def test_date_toggle_adds_a_chip(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        pane = app.query_one(RulesPane)
        assert any(step.label == "Date" for step in pane.steps)
        assert app.rules.add_datetime is True


async def test_command_palette_opens_with_ctrl_p_and_colon(tmp_path: Path) -> None:
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        assert CommandPalette.is_open(app)
        await pilot.press("escape")
        assert not CommandPalette.is_open(app)
        await pilot.press(":")
        assert CommandPalette.is_open(app)


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
