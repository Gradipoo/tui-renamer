"""Push a version tag, wait for that PyPI release, and update the Homebrew tap."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import tomllib
from update_homebrew_formula import pypi_json, update_formula

ROOT = Path(__file__).resolve().parents[1]
PYPI_WAIT_SECONDS = 15 * 60
PYPI_POLL_SECONDS = 20


def main() -> None:
    version = project_version(ROOT)
    require_clean(ROOT)
    require_changelog(ROOT, version)
    require_new_tag(ROOT, version)
    print(f"Pushing v{version}")
    push_tag(ROOT, version)
    print(f"Waiting for tui-renamer=={version} on PyPI")
    wait_for_pypi(version)
    tap = homebrew_tap()
    prepare_tap(tap)
    update_formula(tap / "Formula" / "rn.rb", version=version, use_brew=False)
    push_formula(tap, version)


def project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise SystemExit("pyproject.toml has no project version")
    return version


def require_clean(root: Path) -> None:
    status = git(root, "status", "--porcelain")
    if status.stdout.strip():
        raise SystemExit("tui-renamer has uncommitted changes")


def require_changelog(root: Path, version: str) -> None:
    heading = f"## {version}"
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip() == heading:
            return
    raise SystemExit(f"CHANGELOG.md is missing a heading for {version}")


def require_new_tag(root: Path, version: str) -> None:
    tag = f"v{version}"
    local = git(root, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}", check=False)
    if local.returncode == 0:
        raise SystemExit(f"tag {tag} already exists")
    remote = git(root, "ls-remote", "--exit-code", "origin", f"refs/tags/{tag}", check=False)
    if remote.returncode == 0:
        raise SystemExit(f"tag {tag} already exists")
    if remote.returncode != 2:
        detail = (remote.stderr or remote.stdout).strip()
        raise SystemExit(detail or f"could not check origin for tag {tag}")


def push_tag(root: Path, version: str) -> None:
    tag = f"v{version}"
    git(root, "tag", tag)
    try:
        git(root, "push", "origin", tag)
    except SystemExit:
        git(root, "tag", "-d", tag, check=False)
        raise


def wait_for_pypi(
    version: str,
    *,
    timeout: float = PYPI_WAIT_SECONDS,
    interval: float = PYPI_POLL_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if pypi_json("tui-renamer", version) is not None:
            return
        if time.monotonic() >= deadline:
            raise SystemExit(f"tui-renamer=={version} was not found on PyPI")
        time.sleep(interval)


def homebrew_tap() -> Path:
    result = subprocess.run(
        ["brew", "--repo", "gradipoo/tap"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(detail or "brew --repo gradipoo/tap failed")
    path = Path(result.stdout.strip())
    formula = path / "Formula" / "rn.rb"
    if not formula.is_file():
        raise SystemExit(f"Homebrew tap formula was not found: {formula}")
    return path


def prepare_tap(tap: Path) -> None:
    status = git(tap, "status", "--porcelain")
    if status.stdout.strip():
        raise SystemExit("Homebrew tap has uncommitted changes")
    git(tap, "fetch", "origin", "main")
    for ref in ("HEAD", "main"):
        ahead = git(tap, "rev-list", "--count", ref, "^origin/main")
        if int(ahead.stdout.strip() or "0") != 0:
            raise SystemExit("Homebrew tap has commits that are not on origin/main")
    git(tap, "checkout", "main")
    git(tap, "merge", "--ff-only", "origin/main")


def push_formula(tap: Path, version: str) -> None:
    status = git(tap, "status", "--porcelain", "--", "Formula/rn.rb")
    if not status.stdout.strip():
        print(f"Formula/rn.rb is unchanged for {version}")
        return
    git(tap, "add", "--", "Formula/rn.rb")
    git(tap, "commit", "-m", f"Update rn to {version}")
    git(tap, "push", "origin", "main")
    print(f"Pushed Homebrew formula for {version}")


def git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(detail or f"git {' '.join(args)} failed")
    return result


if __name__ == "__main__":
    main()
