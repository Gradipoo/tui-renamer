"""CLI tests. The app itself is replaced so these do not open a terminal."""

from __future__ import annotations

from pathlib import Path

from tui_renamer import __version__
from tui_renamer.cli import main


class _Stdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_version(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--version should exit")
    assert capsys.readouterr().out.strip() == f"rn {__version__}"


def test_missing_directory(capsys) -> None:
    code = main(["/no/such/rn-directory"])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_starts_the_app(monkeypatch, tmp_path: Path) -> None:
    started: dict[str, object] = {}

    class FakeApp:
        def __init__(self, directory: Path, debug: bool = False) -> None:
            started["directory"] = directory
            started["debug"] = debug

        def run(self) -> None:
            started["ran"] = True

    monkeypatch.setattr("tui_renamer.cli.sys.stdin", _Stdin(True))
    monkeypatch.setattr("tui_renamer.cli.RenamerApp", FakeApp)
    assert main([str(tmp_path), "--debug"]) == 0
    assert started["directory"] == tmp_path
    assert started["debug"] is True
    assert started["ran"] is True


def test_cli_refuses_a_non_tty(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr("tui_renamer.cli.sys.stdin", _Stdin(False))

    def fail_run(self: object) -> None:
        raise AssertionError("run should not be called")

    monkeypatch.setattr("tui_renamer.cli.RenamerApp.run", fail_run)
    assert main([str(tmp_path)]) == 1
    assert capsys.readouterr().err == "Error: rn needs a terminal.\n"


def test_empty_directory_is_still_a_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tui_renamer.cli.sys.stdin", _Stdin(True))
    monkeypatch.setattr("tui_renamer.cli.RenamerApp.run", lambda self: None)
    assert main([str(tmp_path)]) == 0
