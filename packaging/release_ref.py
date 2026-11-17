"""Validate an Actions release ref and expose its version to dependent jobs."""

from __future__ import annotations

import os
from pathlib import Path

from release import project_version, require_changelog

ROOT = Path(__file__).resolve().parents[1]


def release_metadata(root: Path, ref: str) -> dict[str, str]:
    version = project_version(root)
    require_changelog(root, version)
    tag = f"v{version}"
    publish_refs = {f"refs/tags/{tag}", f"refs/heads/release/{version}"}
    if ref.startswith(("refs/tags/", "refs/heads/release/")) and ref not in publish_refs:
        raise SystemExit(f"Release ref {ref} does not match project version {version}")
    return {"version": version, "tag": tag, "publish": str(ref in publish_refs).lower()}


def main() -> None:
    metadata = release_metadata(ROOT, os.environ["GITHUB_REF"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for key, value in metadata.items():
            print(f"{key}={value}", file=handle)


if __name__ == "__main__":
    main()
