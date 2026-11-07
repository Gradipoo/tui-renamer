# Changelog

## Unreleased

- Photo review (`S`) analyzes the highlighted image. Related files are optional. Results are chips you pick for a staged name and supported metadata. Accept writes a notes sidecar. Apply confirms disk rename and metadata. Recognition uses local Ollama only when you ask.
- Photo review lists local vision models (with a loaded mark when Ollama reports one), warns before sending an image to a non-local host, and keeps long setup text in Help. It does not pull models or start the server.
- Photo review does not focus the model field. First use shows installed vision models. A saved local model is read on open. Remote hosts never auto-upload. Findings and related suggestions sit in two columns.
- Photo review shows the original thumbnail beside a highlighted related preview. `r` always reads the original. `R` reads only the highlighted comparison. Name chips append an ordered hyphen suffix on the ordinary rename, including after strip original. Esc keeps the staged name. Overlay Accept is gone. Metadata is a separate chip control. Related files rank by name partners, close stems, and comparable times, strongest first. Locked names stay frozen.
- Photo review `j`/`k` move the focused chip row, related list, or model list. `q` goes back without quitting. Esc leaves a text field first.
- Photo review name tags keep their typed labels. Lowercase, drop symbols, spaces to dashes, and dots to dashes format those tags in the New filename.

## 0.1.3

- HEIC and HEIF capture dates are read when the heif extra is installed. MOV, MP4, and M4V use the QuickTime creation date. That source is Q.
- Preview and zoom are remembered. Ctrl+0 resets them.
- `@` jumps to the previous warning.
- Install from PyPI (`tui-renamer`) or Homebrew (`gradipoo/tap/rn`).

## 0.1.2

- Space and visual mode update the selection in place. They no longer rebuild the file list or reset the scroll.
