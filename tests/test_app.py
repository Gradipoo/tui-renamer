"""Pilot tests for panes, the palette, and one rename plus undo."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.command import CommandPalette
from textual.coordinate import Coordinate
from textual.widgets import DataTable

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
        plain = table.get_cell_at(Coordinate(1, 1))
        assert _is_dim(plain)
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("n")
        assert table.cursor_row == 0
        shown = table.get_cell_at(Coordinate(0, 2))
        assert isinstance(shown, Text)
        assert "..." in shown.plain
        assert shown.plain != f"{stem}.jpg"
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.press("y")
        await pilot.pause()
        assert (tmp_path / f"{stem}.jpg").exists()
        assert not (tmp_path / source_name).exists()


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
