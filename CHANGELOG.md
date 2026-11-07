# Changelog

## Unreleased

- Optional scan review (`S`) for photo fronts and handwritten backs. Suggested pairs, manual pairing, and a notes sidecar. Recognition uses local Ollama only when you ask. It does not write EXIF.
- Scan review recommends current Ollama vision tags, lists local vision models, and warns before sending a back image to a non-local host. It does not pull models or start the server.

## 0.1.3

- HEIC and HEIF capture dates are read when the heif extra is installed. MOV, MP4, and M4V use the QuickTime creation date. That source is Q.
- Preview and zoom are remembered. Ctrl+0 resets them.
- `@` jumps to the previous warning.
- Install from PyPI (`tui-renamer`) or Homebrew (`gradipoo/tap/rn`).

## 0.1.2

- Space and visual mode update the selection in place. They no longer rebuild the file list or reset the scroll.
