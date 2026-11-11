"""Release helper tests. These do not push tags or update a real tap."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))

import release  # noqa: E402
import update_homebrew_formula  # noqa: E402


def test_project_version_reads_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n    version = "0.2.0"\n',
        encoding="utf-8",
    )
    assert release.project_version(tmp_path) == "0.2.0"


def test_pypi_lookup_uses_the_requested_version(monkeypatch) -> None:
    seen: list[str] = []

    def urlopen(url: str, timeout: float = 30):
        seen.append(url)
        return io.BytesIO(b'{"info": {"version": "0.2.0"}}')

    monkeypatch.setattr(update_homebrew_formula.urllib.request, "urlopen", urlopen)
    release.wait_for_pypi("0.2.0", timeout=5, interval=0)
    assert seen == ["https://pypi.org/pypi/tui-renamer/0.2.0/json"]


def test_prepare_tap_refuses_a_dirty_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "tap"
    repo.mkdir()
    _git(repo, "init", "-b", "side")
    _git(repo, "config", "user.email", "rn-test@example.com")
    _git(repo, "config", "user.name", "rn test")
    formula = repo / "Formula" / "rn.rb"
    formula.parent.mkdir()
    formula.write_text("class Rn\n", encoding="utf-8")
    _git(repo, "add", "Formula/rn.rb")
    _git(repo, "commit", "-m", "init")
    head = _git(repo, "rev-parse", "HEAD").strip()
    formula.write_text("class Rn\ndirty\n", encoding="utf-8")

    try:
        release.prepare_tap(repo)
    except SystemExit as exc:
        assert "uncommitted" in str(exc)
    else:
        raise AssertionError("a dirty tap should be refused")

    assert _git(repo, "branch", "--show-current").strip() == "side"
    assert _git(repo, "rev-parse", "HEAD").strip() == head
    assert formula.read_text(encoding="utf-8") == "class Rn\ndirty\n"


def test_update_formula_skips_brew_when_asked(monkeypatch, tmp_path: Path) -> None:
    formula = tmp_path / "rn.rb"
    formula.write_text(
        "\n".join(
            [
                "class Rn < Formula",
                '  url "https://example.invalid/old.tar.gz"',
                '  sha256 "0000000000000000000000000000000000000000000000000000000000000000"',
                "  # BEGIN resources",
                "  # END resources",
                "end",
                "",
            ]
        ),
        encoding="utf-8",
    )
    payload = {
        "info": {"name": "tui-renamer", "version": "0.2.0"},
        "urls": [
            {
                "packagetype": "sdist",
                "url": "https://files.example/tui_renamer-0.2.0.tar.gz",
                "digests": {"sha256": "ab" * 32},
            }
        ],
    }
    monkeypatch.setattr(
        update_homebrew_formula,
        "pypi_json",
        lambda name, version=None: payload,
    )
    monkeypatch.setattr(
        update_homebrew_formula,
        "resource_blocks",
        lambda root: ['  resource "textual-image" do\n  end\n'],
    )
    monkeypatch.setattr(update_homebrew_formula.shutil, "which", lambda name: "/usr/bin/brew")

    def refuse_subprocess(*args, **kwargs):
        raise AssertionError(args[0])

    monkeypatch.setattr(update_homebrew_formula.subprocess, "run", refuse_subprocess)
    update_homebrew_formula.update_formula(formula, version="0.2.0", use_brew=False)
    text = formula.read_text(encoding="utf-8")
    assert 'url "https://files.example/tui_renamer-0.2.0.tar.gz"' in text
    assert 'resource "textual-image"' in text


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
