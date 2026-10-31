"""Fill Formula/rn.rb from PyPI.

The tui-renamer sdist url and sha256 stay as placeholders until that project
is published. Resource blocks come from `uv pip compile` of this repo with the
heif extra (or a pip dry-run when uv is missing), then each package's sdist
on PyPI.

After the first release, pass the formula path. If `tui-renamer` is on PyPI
and `brew` is on PATH, this runs `brew update-python-resources` instead.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMULA = ROOT / "packaging" / "homebrew-tap" / "Formula" / "rn.rb"
BEGIN = "# BEGIN resources"
END = "# END resources"


def main() -> None:
    formula = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FORMULA
    text = formula.read_text(encoding="utf-8")
    published = pypi_json("tui-renamer")
    if published is not None:
        url, digest = sdist(published)
        text = replace_header(text, url, digest)
        formula.write_text(text, encoding="utf-8")
        if shutil.which("brew"):
            result = subprocess.run(
                ["brew", "update-python-resources", str(formula)],
                check=False,
            )
            if result.returncode == 0:
                print(f"Updated {formula} with brew update-python-resources")
                return
            print("brew update-python-resources failed. Filling resources from PyPI.")
    else:
        print("tui-renamer is not on PyPI. Leaving the sdist url as a placeholder.")
    blocks = resource_blocks(ROOT)
    text = formula.read_text(encoding="utf-8")
    formula.write_text(replace_resources(text, blocks), encoding="utf-8")
    print(f"Wrote {len(blocks)} resources to {formula}")


def pypi_json(name: str, version: str | None = None) -> dict | None:
    if version:
        url = f"https://pypi.org/pypi/{name}/{version}/json"
    else:
        url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        return None
    return payload


def sdist(payload: dict) -> tuple[str, str]:
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise SystemExit("PyPI payload has no urls")
    for item in urls:
        if isinstance(item, dict) and item.get("packagetype") == "sdist":
            digests = item.get("digests") or {}
            digest = digests.get("sha256")
            url = item.get("url")
            if isinstance(url, str) and isinstance(digest, str):
                return url, digest
    name = payload.get("info", {}).get("name", "package")
    raise SystemExit(f"{name} has no sdist on PyPI")


def replace_header(text: str, url: str, digest: str) -> str:
    text, url_count = re.subn(r'^  url ".*"$', f'  url "{url}"', text, count=1, flags=re.M)
    text, sha_count = re.subn(
        r'^  sha256 "[0-9a-f]+"$',
        f'  sha256 "{digest}"',
        text,
        count=1,
        flags=re.M,
    )
    if url_count != 1 or sha_count != 1:
        raise SystemExit("Could not find the formula url and sha256 lines")
    return text


def replace_resources(text: str, blocks: list[str]) -> str:
    start = text.find(BEGIN)
    stop = text.find(END)
    if start < 0 or stop < 0 or stop < start:
        raise SystemExit(f"Formula is missing {BEGIN} / {END} markers")
    body = "\n".join(blocks)
    middle = f"{BEGIN}\n{body}\n  " if body else f"{BEGIN}\n  "
    return text[:start] + middle + text[stop:]


def resource_blocks(root: Path) -> list[str]:
    packages = pip_packages(root)
    blocks: list[str] = []
    for name, version in sorted(packages):
        payload = pypi_json(name, version)
        if payload is None:
            raise SystemExit(f"{name}=={version} was not found on PyPI")
        url, digest = sdist(payload)
        blocks.append(
            "\n".join(
                [
                    f'  resource "{name}" do',
                    f'    url "{url}"',
                    f'    sha256 "{digest}"',
                    "  end",
                    "",
                ]
            )
        )
    return blocks


def pip_packages(root: Path) -> list[tuple[str, str]]:
    if shutil.which("uv"):
        return uv_packages(root)
    return pip_report_packages(root)


def uv_packages(root: Path) -> list[tuple[str, str]]:
    """Resolve for Python 3.12, the version the formula depends on."""
    result = subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            "pyproject.toml",
            "--extra",
            "heif",
            "--python-version",
            "3.12",
            "--no-header",
            "--no-annotate",
            "-o",
            "-",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    found: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        piece = line.strip()
        if not piece or piece.startswith("#") or "==" not in piece:
            continue
        raw_name, version = piece.split("==", 1)
        name = normalize(raw_name.split("[", 1)[0])
        version = version.split(" ", 1)[0].split(";", 1)[0]
        if name != "tui-renamer":
            found.append((name, version))
    if not found:
        raise SystemExit("uv listed no dependencies")
    return found


def pip_report_packages(root: Path) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--disable-pip-version-check",
                "--report",
                str(report_path),
                ".[heif]",
            ],
            cwd=root,
            check=True,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    install = report.get("install")
    if not isinstance(install, list):
        raise SystemExit("pip report has no install list")
    for item in install:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata")
        if not isinstance(meta, dict):
            continue
        raw_name = meta.get("name")
        version = meta.get("version")
        if not isinstance(raw_name, str) or not isinstance(version, str):
            continue
        name = normalize(raw_name)
        if name == "tui-renamer":
            continue
        found.append((name, version))
    if not found:
        raise SystemExit("pip report listed no dependencies")
    return found


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


if __name__ == "__main__":
    main()
