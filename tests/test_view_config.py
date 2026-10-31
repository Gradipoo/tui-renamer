"""View config stays off the real home directory."""

from __future__ import annotations

import json
from pathlib import Path

from tui_renamer import view_config
from tui_renamer.view_config import ViewState, clear_view, load_view, save_view

real_config_dir = view_config.config_dir


def test_config_dir_follows_the_platform(monkeypatch) -> None:
    monkeypatch.setattr("tui_renamer.view_config.sys.platform", "darwin")
    monkeypatch.setattr("tui_renamer.view_config.Path.home", lambda: Path("/Users/sean"))
    assert real_config_dir() == Path("/Users/sean/Library/Application Support/rn")

    monkeypatch.setattr("tui_renamer.view_config.sys.platform", "linux")
    monkeypatch.setattr("tui_renamer.view_config.Path.home", lambda: Path("/home/sean"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert real_config_dir() == Path("/home/sean/.config/rn")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert real_config_dir() == Path("/xdg/rn")

    monkeypatch.setattr("tui_renamer.view_config.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\sean\AppData\Roaming")
    assert real_config_dir() == Path(r"C:\Users\sean\AppData\Roaming") / "rn"


def test_round_trip_and_reset(monkeypatch, tmp_path: Path) -> None:
    folder = tmp_path / "cfg"
    monkeypatch.setattr("tui_renamer.view_config.config_dir", lambda: folder)
    assert load_view() == ViewState()
    save_view(ViewState(preview=False, zoom="files"))
    assert json.loads((folder / "view.json").read_text(encoding="utf-8")) == {
        "preview": False,
        "zoom": "files",
    }
    assert load_view() == ViewState(preview=False, zoom="files")
    clear_view()
    assert load_view() == ViewState()
    assert not (folder / "view.json").exists()


def test_broken_file_and_bad_zoom_use_defaults(monkeypatch, tmp_path: Path) -> None:
    folder = tmp_path / "cfg"
    folder.mkdir()
    monkeypatch.setattr("tui_renamer.view_config.config_dir", lambda: folder)
    (folder / "view.json").write_text("{", encoding="utf-8")
    assert load_view() == ViewState()
    (folder / "view.json").write_text(
        json.dumps({"preview": "no", "zoom": "sideways"}),
        encoding="utf-8",
    )
    assert load_view() == ViewState()


def test_unwritable_config_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr("tui_renamer.view_config.config_dir", lambda: blocker / "rn")
    save_view(ViewState(preview=False, zoom="detail"))
    clear_view()
    assert load_view() == ViewState()
