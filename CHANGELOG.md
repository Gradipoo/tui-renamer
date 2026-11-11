# Changelog

## Unreleased

- Sort the file table by the resolved new name, ascending or descending. Folder order, neighbor warnings, and collision suffixes stay put.
- Show collision suffixes in the New column. A trailing `-NN` is the same base when another file shares that stem. A lone `holiday-01` keeps its suffix.
- Replace the Src column with Fit: `ok`, `rn`, or `dup` for names, and the date or file-time equivalents. The date source stays in the detail pane.
- Preview the first GIF frame, one ffmpeg frame for MOV, MP4, and M4V, JPEG XL with the optional `jxl` extra, and the embedded JPEG in Canon CR3. CR3 and JPEG XL metadata are not written.

## 0.1.9

- Local System filename stamps use the system zone's daylight offset for the capture date. An October UTC time no longer shifts by today's standard offset.

## 0.1.8

- Filter and sort the file table with `f`, the Filter and sort button, or the command palette. Combine filters for missing capture dates, date disagreements, warnings, changed filenames, pending metadata, and extension.
- Search, navigation, and range selection follow the visible rows. Apply still includes hidden staged work and lists those targets in the confirmation.
- Group the main controls by action and show the current Apply and undo scope.

## 0.1.7

- Add a visible Use earliest date button and `E` shortcut to stage each file's earliest eligible date in its filename.
- Show the highlighted file's earliest date and source even with the detail pane hidden, plus a hint when a manually chosen date is newer.
- Keep filename staging separate from metadata writes and preserve locks, manual edits, and Apply confirmation.

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
