# Notes

## Discovered facts

- Repo was empty. `origin` is `git@github.com:Gradipoo/tui-renamer.git`.
- Python floor is 3.12. `textual-image[textual]` is a normal dependency. `pillow-heif` is the `heif` extra.
- This machine: `.venv` is Python 3.11 and cannot install textual-image. Tests use `.venv314` (Homebrew 3.14). `.venv*/` is gitignored.
- Do not import `textual_image.widget` at import time. It probes the terminal. `cli.main` calls `warm_image_widget()` after the tty check, and that function skips itself when `PYTEST_CURRENT_TEST` is set.
- CI tests Python 3.12 and 3.13 on Ubuntu, macOS, and Windows. Lint stays on 3.12.
- Install: `python -m pip install -e ".[dev]"` from a venv.
- Test: `python -m pytest`
- Lint: `ruff check .` and `ruff format --check .`
- Entry point: `rn` -> `tui_renamer.cli:main`
- Modelled on Gradipoo/tui-jxl-converter: src layout, textual>=3, GPL-3.0-only, pytest-asyncio auto, ruff E/F/I/UP, line length 100, CI on Ubuntu/macOS/Windows, PyInstaller on tag `v*`.
- Non-tty stdin exits 1 with `Error: rn needs a terminal.`
- Linux: every 0.5s, if `/proc/self/fd/<stdin>` ends with ` (deleted)`, exit.
- `tzdata` is a dependency so zoneinfo works on Windows.
- `exiftool` and macOS `SetFile` stay external. JPEG/TIFF use piexif.
- Offset tags: 36880 OffsetTime, 36881 OffsetTimeOriginal, 36882 OffsetTimeDigitized. Read OffsetTimeOriginal, then OffsetTime, from the Exif IFD (0x8769).
- Undo is session-only. Temp copies live under the system temp dir and are removed on exit.

## Project overrides

None.

## Known gotchas

- `piexif.load` on a JPEG Pillow just saved still returns a tiny EXIF blob, so "no exif" is not the same as `had_exif is False`.
- Filename date planning uses `source_tz is None` for the system zone. Passing the local tzinfo is not the same call.
- The dashed timestamp strip (`YYYY-MM-DD-HHMMSS` at either end) always runs, even when every toggle is off.
- `Widget.remove()` only schedules a prune. The id stays until that finishes. Await the removal before mounting another widget with the same id, or Textual raises `DuplicateIds`.
- A Textual `Checkbox` or `Button` is 3 lines tall because of its border. Forcing `height: 1` paints only the top border. Use `compact=True`, or a one-line label, and give the strip enough height for its border plus those lines.
