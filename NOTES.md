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

- Resize handlers must use both dimensions from the Resize event and update layout classes even during a table rebuild. The app size can still reflect the previous terminal height. Reproduce compact layouts without optional metadata tools, whose startup message consumes extra rows.
- Column fitting waits for queued cursor highlights instead of rebuilding from an outdated app cursor. Completing a rebuild reconciles movement that arrived while highlights were suppressed. Ignore stale highlights whose row or key no longer matches the live table, and highlights from detached tables during shutdown.
- Explicit table navigation invalidates deferred scroll restoration. A late restore from before filtering or sorting can otherwise move the highlight offscreen, even when the selected file and row are correct. Each rebuild also invalidates older restores, including rebuilds at scroll zero. Changing filters or sort order resets the previous viewport before rebuilding and reveals the selected file afterward.
- Toggle existing photo-review chip buttons in place. Replacing them on every press loses focus and can drop queued presses while old widgets are being removed.

- `datetime.fromtimestamp` on Windows raises `OSError: [Errno 22]` for some pre-1970 `st_birthtime` values after a written-day capture write. `utc_from_timestamp` returns None instead of crashing inspect/preview.
- Windows `Path.rename` refuses to replace an existing destination. Journal undo uses `os.replace`, then copy plus unlink if replace is blocked (file in use). Undo also drops the preview widget and cache first so the image file is not held open.
- `piexif.load` on a JPEG Pillow just saved still returns a tiny EXIF blob, so "no exif" is not the same as `had_exif is False`.
- Filename date planning uses `source_tz is None` for the system zone. Passing the local tzinfo is not the same call.
- The dashed timestamp strip (`YYYY-MM-DD-HHMMSS` at either end) always runs, even when every toggle is off.
- `Widget.remove()` only schedules a prune. The id stays until that finishes. Await the removal before mounting another widget with the same id, or Textual raises `DuplicateIds`.
- A Textual `Checkbox` or `Button` is 3 lines tall because of its border. Forcing `height: 1` paints only the top border. Use `compact=True`, or a one-line label, and give the strip enough height for its border plus those lines.
- Agent shells set `NO_COLOR=1`. Textual then applies a monochrome filter, so `export_screenshot` is gray even when rsvg-convert is used. Clear `NO_COLOR` before constructing the app. Headless `AutoImage` is the unicode dot renderer. The screenshot script assigns `HalfcellImage` instead.
- Name/New widths must be computed after layout. Zoom only changes CSS, so `_fill_table` during the keypress still sees the old pane. Fit from `call_after_refresh`. Do not hook `FileTable.on_resize`: the find bar changes height and a refill there fights the cursor.
- Do not `clear()` the file table on selection or visual moves. `clear()` resets scroll, and the highlight it posts (often row 0) is handled after `_rebuilding` drops, so visual mode treats it as a new range and rebuilds again. Update those cells in place. A real rebuild keeps `_rebuilding` set until `call_after_refresh`, and restores scroll after the cursor watcher. Do not schedule a scroll restore of `(0, 0)`; a deferred one can wipe a later scroll on slow CI.
- QuickTime dates come from `com.apple.quicktime.creationdate` when that string has a timezone, otherwise `mvhd` (1904 epoch, UTC). Letter Q does not set `has_exif`. HEIC uses the EXIF reader only when `heif_ready()` is true. The top-level box walk must seek back to the payload. The header read is 16 bytes, and a normal box header is 8.
- Preview and zoom are stored in `view.json` under the platform config dir (`~/Library/Application Support/rn` on macOS). Tests patch `tui_renamer.view_config.config_dir`. A failed write must not block the key. The previous-warning key is `@`, which Textual normalizes to `at`.
- Scan notes live in `<image.name>.rn-notes.json` with `"format": "rn-notes"` (version 2, still loads 1). `load_facts` skips those files when looking up Google Takeout JSON. Ollama is POST `/api/generate` with `images` and a JSON schema. Local discovery is GET `/api/tags` plus POST `/api/show` for `vision` capability, loopback only. Loaded models are GET `/api/ps`. Capture EXIF is only a written calendar day (optional noon). Year, month, season, inferred, and ambiguous dates never become EXIF. Description is JPEG/TIFF ImageDescription. Recommended tags (checked on the official library, not accuracy-tested): light `qwen3.5:2b`, default `gemma3:4b`, larger `gemma4:e4b`. `gemma3:270m` and `gemma3:1b` are text-only. Tests use `MockBackend` only. Photo review auto-reads a saved local vision model on open. It never auto-uploads to a remote host. Related candidates use filename partners, close stems, numeric neighbors, and comparable clocks. Capture closeness uses `has_exif` capture candidates, not `origtime` (macOS birth fallback). mtime and birth stay labeled as file times. Size and time-only clues are never Likely. Within a tier, rank is strength then name. Existing-capture skip uses `has_exif`, not macOS birth time. Highlighting a related file updates the comparison thumbnail only. The first candidate is previewed. `r` reads the original. `R` reads the highlight. Name chips are on/off and stage `scan_suffix` immediately. Metadata is a separate toggle. Overlay Accept is gone. Notes sidecars write on Apply. Lock freezes the proposed name, including ignoring subsequent S suffix changes. A metadata-only Apply writes notes, but undo may leave a newly created sidecar. Batch notes preserve this limitation in the result and undo report. Photo review `j`/`k` move the focused list. `q` closes the overlay. Esc leaves a text field first. S name chips store raw labels in `scan_suffix`. Filename format toggles run through `format_scan_parts`/`clean_filename` at preview. Apply stores formatted `scan_accepted` and sidecar `name_suffix` for strip/remap. Session bits stay raw. Written calendar-day chips in a name use `filename_clock_text` (`YYYY-MM-DD-HHMMSS`). Date-only uses 12:00. That noon is assumed. Partial, inferred, and uncertain dates stay as chip text. Whole-label English/ISO calendar days (`parse_whole_calendar_day`) stamp tokens, typed labels, and stored suffix strings. Substring dates in a sentence do not. Filename parse does not set `use_meta`.
- File-table rows use `_visible` to map display positions to canonical `self.files` indexes. Selection, locks, manual names, date plans, and batch execution keep canonical indexes. Never reorder `self.files` to sort the table: it changes collision suffix order and neighbor warnings. Search and visual ranges follow `_visible`.
