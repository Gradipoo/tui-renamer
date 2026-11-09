# Changelog

## Unreleased

## 0.1.6

- Update release version to v0.1.6.

## 0.1.4

- Photo review (`S`) with optional local Ollama vision. Ollama and the model are a separate install. `rn` does not download models at runtime. Real handwriting accuracy is not guaranteed.
- Ordered selectable name tags follow filename options. A whole-label recognized calendar day uses the ordinary YYYY-MM-DD-HHMMSS stamp.
- Original and related previews, ranked related files, and vim `j`/`k`/`q` in the overlay.
- Windows: skip unreadable pre-1970 file times, and restore on undo when the original name is blocked.

## 0.1.3

- HEIC and HEIF capture dates are read when the heif extra is installed. MOV, MP4, and M4V use the QuickTime creation date. That source is Q.
- Preview and zoom are remembered. Ctrl+0 resets them.
- `@` jumps to the previous warning.
- Install from PyPI (`tui-renamer`) or Homebrew (`gradipoo/tap/rn`).

## 0.1.2

- Space and visual mode update the selection in place. They no longer rebuild the file list or reset the scroll.
