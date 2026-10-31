# rn

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Made with Textual](https://img.shields.io/badge/made%20with-Textual-green.svg)

A fast, keyboard-driven terminal UI for batch renaming photos and files by EXIF date, timezone, and geodata, with live preview and undo.

Run it inside the folder you want to rename, or pass a path. `rn` works on macOS, Linux, and Windows.

## Install

### pipx or uv

```bash
pipx install git+https://github.com/Gradipoo/tui-renamer.git
```

```bash
uv tool install git+https://github.com/Gradipoo/tui-renamer.git
```

### From source

```bash
git clone https://github.com/Gradipoo/tui-renamer.git
cd tui-renamer
python -m venv .venv
source .venv/bin/activate
pip install -e .
rn
```

On Windows, activate with `.venv\Scripts\activate`.

### Download a release

Prebuilt programs are attached to [GitHub Releases](https://github.com/Gradipoo/tui-renamer/releases) when a version tag such as `v0.1.0` is pushed.

| File | System |
| --- | --- |
| `rn-linux-x86_64` | Linux, 64-bit |
| `rn-windows-x64.exe` | Windows, 64-bit |
| `rn-macos-arm64` | macOS, Apple Silicon |

The Linux file is built on GitHub's current Ubuntu runner, so it needs a similarly recent glibc. Older distributions should use pipx or uv instead.

The macOS build is not signed or notarized. The first open is blocked until you allow it:

1. In Finder, right-click `rn-macos-arm64` and choose Open, then Open again.
2. Or, in Terminal: `xattr -d com.apple.quarantine rn-macos-arm64`

Windows SmartScreen may warn on the first launch because the file is not signed. Choose More info, then Run anyway.

Intel Macs are not in the release build. Use pipx or uv on those machines.

Make the Linux and macOS files executable (`chmod +x`) if the download did not keep that bit.

Pillow, piexif, and the IANA timezone data are included. `exiftool` and macOS `SetFile` are not.

## exiftool and birth time

JPEG and TIFF date and GPS tags are written with piexif. Video (`.mov`, `.mp4`, `.m4v`), HEIC, and PNG need `exiftool` on `PATH`.

| System | Package | Command |
| --- | --- | --- |
| macOS | Homebrew | `brew install exiftool` |
| Arch | pacman | `sudo pacman -S perl-image-exiftool` |
| Debian, Ubuntu | apt | `sudo apt-get install -y libimage-exiftool-perl` |
| Fedora | dnf | `sudo dnf install -y perl-Image-ExifTool` |

On Windows, install [ExifTool](https://exiftool.org/) and put `exiftool` on `PATH`.

Birth time (the creation time photos apps show) is set on macOS with `SetFile` from the Xcode command line tools (`xcode-select --install`), and on Windows with the system file-time call. Linux updates mtime and metadata only. If `SetFile` is missing, macOS still updates mtime and EXIF.

## Usage

```bash
rn
rn ~/Pictures/trip
rn -d
```

`-d` appends a log to `renamer_debug.log` in the folder you opened.

`rn` reads the terminal. If stdin is not a terminal, it exits. On Linux it also exits if the terminal device goes away, so closing the window does not leave the process running.

Hidden files and `.json` sidecars are not listed. Sidecars are read when JSON mode is on. The search matches Google Takeout names, including `name.jpg.json`.

An empty folder still opens. Press `r` after you add files.

## Keys

| Key | Action |
| --- | --- |
| `1` / `2` / `3` | Focus files, detail, or rules |
| Up/Down, `j`/`k` | Move |
| `g` then `g` / `G` | First file / last file |
| PageUp / PageDown | Page |
| Space | Select or clear this file |
| `v` | Select a range with up and down |
| `a` / `A` | Select all / select none |
| `d` `m` `o` | Add today's time, modified time, original (EXIF) time |
| `e` | Read times and location from a `.json` sidecar |
| `b` | Add the JSON location to the name |
| `t` | Timestamps at the start, or the end |
| `x` / `y` | Drop the original name / keep letters and spaces |
| `c` `s` `p` `.` | Lowercase, drop symbols, spaces to dashes, dots to dashes |
| `0` | Reset name options |
| `i` | Edit this name. Esc saves. Enter saves and renames |
| `l` / `L` / `U` | Lock this file / lock selected / unlock all |
| Enter | Apply the current mode. Asks first |
| `D` | Filename date mode. Enter sets dates and EXIF |
| `M` | Set mtime mode. Enter sets times |
| `W` | Write the local time and timezone offset |
| `B` | Write JSON location into EXIF GPS |
| `T` | Choose a timezone |
| `u` | Undo the last batch (this session only) |
| `r` | Reload the folder |
| `:` or Ctrl+P | Command palette |
| `?`, `h`, F1 | Help |
| `q` | Quit |

While `g` is waiting, the status line shows `g then g: first file`.

The rules strip is the pipeline that builds the new name. Only steps that are on are shown. In that pane, Left and Right move, and Enter toggles the highlighted step.

The detail pane shows every date candidate for the highlighted file, which one wins, the zone, geodata, and a character diff of the old and new name. Insertions are green. Deletions are red.

`!` means the timestamp you chose is not the oldest of modified and original.

Locked names stay put when you change toggles. A manual edit is used until you lock that file. Refresh clears selection, locks, and manual edits.

Filename dates understand `2024-01-31-142500`, `IMG_20240131_142500`, `2024-01-31 14.25.00`, `2024-01-31_1425`, and `20240131`. A date with no time becomes 12:00, unless the file's calendar day already matches, in which case the clock is left alone. If two files land on the same second, the later one is nudged by one second.

Undo remembers renames and metadata writes until you quit. JPEG and TIFF EXIF is restored from the previous segment. Files written with `exiftool` are copied to a temp folder first. If that copy fails, the write still happens and the status line says that file cannot be undone.

## License

Copyright (C) 2026 Gradipoo.

GNU General Public License v3.0. You can use, change, and share this program. If you distribute a modified version, or a program that includes this code, that program must also be under GPLv3 and you must offer the source. See [LICENSE](LICENSE).
