"""Command list for the merged help and command overlay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    group: str
    key: str
    label: str
    description: str
    run: str
    state: str = ""


def _spec(
    group: str,
    key: str,
    label: str,
    description: str,
    run: str,
    state: str = "",
) -> CommandSpec:
    return CommandSpec(group, key, label, description, run, state)


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    _spec("Move", "f", "Filter and sort", "Filter files and choose table order", "filter_sort"),
    _spec("Move", "k / up", "Up", "Move up", "cursor_up"),
    _spec("Move", "j / down", "Down", "Move down", "cursor_down"),
    _spec("Move", "pageup", "Page up", "Page up", "page_up"),
    _spec("Move", "pagedown", "Page down", "Page down", "page_down"),
    _spec("Move", "g g", "Top", "Jump to the first file", "goto_top"),
    _spec("Move", "G", "Bottom", "Jump to the last file", "goto_end"),
    _spec("Move", "/", "Find", "Find in the current and new name", "find"),
    _spec("Move", "n", "Next", "Next match, or the next file that will change", "next_change"),
    _spec(
        "Move",
        "N",
        "Previous",
        "Previous match, or the previous file that will change",
        "prev_change",
    ),
    _spec("Move", "]", "Next change", "Next file that will change", "next_changed"),
    _spec("Move", "[", "Previous change", "Previous file that will change", "prev_changed"),
    _spec("Move", "ctrl+g", "Find next", "Next search match", "search_again"),
    _spec("Move", "!", "Next warning", "Next warning", "next_warning"),
    _spec("Move", "@", "Previous warning", "Previous warning", "prev_warning"),
    _spec(
        "Staged edits and selection",
        "space",
        "Staged edits and selection",
        "Select or clear this file",
        "toggle_select",
    ),
    _spec("Staged edits and selection", "v", "Visual", "Select a range", "visual", "visual"),
    _spec("Staged edits and selection", "a", "All", "Select all", "select_all"),
    _spec(
        "Staged edits and selection",
        "A",
        "Clear selection",
        "Clear visible and hidden selection; keep staged edits",
        "select_none",
    ),
    _spec(
        "Filename preview",
        "E",
        "Use earliest date",
        "Stage each file’s earliest available date",
        "toggle:use_earliest",
        "use_earliest",
    ),
    _spec(
        "Filename preview",
        "d",
        "Date",
        "Add today's date and time",
        "toggle:add_datetime",
        "add_datetime",
    ),
    _spec(
        "Filename preview", "m", "Mod", "Add the modified time", "toggle:add_modtime", "add_modtime"
    ),
    _spec(
        "Filename preview",
        "o",
        "Orig",
        "Add the original time",
        "toggle:add_origtime",
        "add_origtime",
    ),
    _spec(
        "Filename preview",
        "e",
        "JSON",
        "Read a sidecar JSON file",
        "toggle:use_json_data",
        "use_json_data",
    ),
    _spec(
        "Filename preview",
        "b",
        "Geo",
        "Add JSON location to the name",
        "toggle:add_geodata",
        "add_geodata",
    ),
    _spec(
        "Filename preview",
        "t",
        "Prefix",
        "Times at the start when on",
        "toggle:use_prefix",
        "use_prefix",
    ),
    _spec(
        "Filename cleanup",
        "x",
        "Strip",
        "Drop the original name",
        "toggle:strip_original_name",
        "strip_original_name",
    ),
    _spec(
        "Filename cleanup",
        "y",
        "Text",
        "Keep letters and spaces",
        "toggle:strip_to_text",
        "strip_to_text",
    ),
    _spec(
        "Filename cleanup", "c", "lower", "Lowercase the name", "toggle:remove_caps", "remove_caps"
    ),
    _spec(
        "Filename cleanup",
        "s",
        "Symbols",
        "Remove symbols",
        "toggle:remove_special",
        "remove_special",
    ),
    _spec(
        "Filename cleanup",
        "p",
        "Dashes",
        "Spaces become dashes",
        "toggle:replace_spaces",
        "replace_spaces",
    ),
    _spec("Filename cleanup", ".", "Dots", "Dots to dashes", "toggle:replace_dots", "replace_dots"),
    _spec(
        "Filename preview",
        "0",
        "Reset naming rules",
        "Keep manual names, locks and photo-review staging",
        "reset_rules",
    ),
    _spec(
        "Staged edits and selection", "i", "Staged edits and selection", "Edit this name", "insert"
    ),
    _spec("Staged edits and selection", "l", "Lock", "Lock this file", "lock"),
    _spec("Staged edits and selection", "L", "Lock all", "Lock selected files", "lock_selected"),
    _spec("Staged edits and selection", "U", "Unlock", "Unlock all", "unlock_all"),
    _spec("Batch actions", "enter", "Apply", "Apply the current mode", "apply"),
    _spec("Apply mode", "D", "Filename date", "Set dates from filenames", "date_mode", "date_mode"),
    _spec(
        "Write after confirmation",
        "W",
        "Write zone",
        "Write date and timezone offset",
        "write_timezone",
    ),
    _spec("Apply mode", "M", "mtime", "Set mtime mode", "mtime_mode", "mtime_mode"),
    _spec(
        "Write after confirmation",
        "B",
        "Write GPS",
        "Write JSON location into EXIF",
        "write_geodata",
    ),
    _spec("Filename preview", "T", "Timezone", "Choose a timezone", "timezone"),
    _spec("Batch actions", "u", "Undo", "Undo the last recorded batch; session only", "undo"),
    _spec(
        "Batch actions",
        "",
        "Last result",
        "Inspect affected files, failures and undo coverage",
        "last_result",
    ),
    _spec(
        "Staged edits and selection",
        "",
        "Discard manual name",
        "Ask before discarding this file's staged manual name",
        "discard_manual_name",
    ),
    _spec("View and session", "P", "Preview", "Show or hide the preview", "preview", "preview_on"),
    _spec(
        "Staged edits and selection",
        "S",
        "Photo review",
        "Review this photo. j/k move, q back. Ollama is optional",
        "scan_review",
    ),
    _spec("View and session", "ctrl+0", "Reset view", "Reset preview and zoom", "reset_view"),
    _spec("View and session", "r", "Refresh", "Reload the folder", "reload_files"),
    _spec("Actions", "?", "Help", "Show commands", "help"),
    _spec("Actions", "q", "Quit", "Quit", "quit"),
)

FILE_ACTIONS = {
    "apply",
    "insert",
    "discard_manual_name",
    "lock",
    "lock_selected",
    "date_mode",
    "mtime_mode",
    "write_timezone",
    "write_geodata",
    "scan_review",
}


@dataclass(frozen=True)
class CommandContext:
    file_count: int
    selected: int
    locked: int
    undo_count: int
    date_mode: bool
    mtime_mode: bool


def dim_reason(spec: CommandSpec, ctx: CommandContext) -> str:
    """Why a command cannot run, or an empty string when it can."""
    run = spec.run
    if run in {"lock", "lock_selected", "insert"} and (ctx.date_mode or ctx.mtime_mode):
        return "mode clash"
    if run == "date_mode" and ctx.mtime_mode:
        return "mode clash"
    if run == "mtime_mode" and ctx.date_mode:
        return "mode clash"
    if run == "undo" and ctx.undo_count == 0:
        return "nothing to undo"
    if run == "unlock_all" and ctx.locked == 0:
        return "nothing locked"
    if run == "lock_selected" and ctx.selected == 0:
        return "no selection"
    if run in FILE_ACTIONS and ctx.file_count == 0:
        return "no files"
    return ""


def fuzzy_score(query: str, text: str) -> int:
    """Subsequence score. An empty query matches. Uppercase is case sensitive."""
    if not query:
        return 1
    sensitive = any(char.isupper() for char in query)
    haystack = text if sensitive else text.casefold()
    needle = query if sensitive else query.casefold()
    if needle in haystack:
        return 100 + max(0, 40 - haystack.find(needle))
    score = 0
    pos = 0
    for char in needle:
        found = haystack.find(char, pos)
        if found < 0:
            return 0
        gap = found - pos
        score += 20 if gap == 0 else max(1, 10 - gap)
        pos = found + 1
    return score


def matching_commands(query: str, ctx: CommandContext) -> list[tuple[CommandSpec, str]]:
    """Specs that match, best score first, with a dim reason."""
    ranked: list[tuple[int, int, CommandSpec, str]] = []
    for index, spec in enumerate(COMMAND_SPECS):
        blob = f"{spec.key} {spec.label} {spec.description} {spec.group}"
        score = fuzzy_score(query, blob)
        label = spec.label if any(char.isupper() for char in query) else spec.label.casefold()
        needle = query if any(char.isupper() for char in query) else query.casefold()
        if needle and label == needle:
            score += 200
        elif needle and label.startswith(needle):
            score += 80
        if score <= 0:
            continue
        ranked.append((score, -index, spec, dim_reason(spec, ctx)))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(spec, reason) for _score, _index, spec, reason in ranked]
