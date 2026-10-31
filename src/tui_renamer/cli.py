"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tui_renamer.app import RenamerApp
from tui_renamer.preview import warm_image_widget


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rn",
        description="Batch rename photos and files by EXIF date, timezone, and geodata.",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Folder to rename. Defaults to the current directory.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Append a log to renamer_debug.log in that folder.",
    )
    args = parser.parse_args(argv)
    directory = Path(args.directory).expanduser()
    if not directory.is_dir():
        print(f"Error: directory not found: {directory}", file=sys.stderr)
        return 1
    if not sys.stdin.isatty():
        print("Error: rn needs a terminal.", file=sys.stderr)
        return 1
    warm_image_widget()
    RenamerApp(directory, debug=args.debug).run()
    return 0
