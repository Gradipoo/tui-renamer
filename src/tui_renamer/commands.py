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
    _spec("Select", "space", "Select", "Select or clear this file", "toggle_select"),
    _spec("Select", "v", "Visual", "Select a range", "visual", "visual"),
    _spec("Select", "a", "All", "Select all", "select_all"),
    _spec("Select", "A", "None", "Select none", "select_none"),
    _spec("Name", "d", "Date", "Add today's date and time", "toggle:add_datetime", "add_datetime"),
    _spec("Name", "m", "Mod", "Add the modified time", "toggle:add_modtime", "add_modtime"),
    _spec("Name", "o", "Orig", "Add the original time", "toggle:add_origtime", "add_origtime"),
    _spec("Name", "e", "JSON", "Read a sidecar JSON file", "toggle:use_json_data", "use_json_data"),
    _spec("Name", "b", "Geo", "Add JSON location to the name", "toggle:add_geodata", "add_geodata"),
    _spec("Name", "t", "Prefix", "Times at the start when on", "toggle:use_prefix", "use_prefix"),
    _spec(
        "Name",
        "x",
        "Strip",
        "Drop the original name",
        "toggle:strip_original_name",
        "strip_original_name",
    ),
    _spec("Name", "y", "Text", "Keep letters and spaces", "toggle:strip_to_text", "strip_to_text"),
    _spec("Name", "c", "lower", "Lowercase the name", "toggle:remove_caps", "remove_caps"),
    _spec("Name", "s", "Symbols", "Remove symbols", "toggle:remove_special", "remove_special"),
    _spec("Name", "p", "Dashes", "Spaces become dashes", "toggle:replace_spaces", "replace_spaces"),
    _spec("Name", ".", "Dots", "Dots to dashes", "toggle:replace_dots", "replace_dots"),
    _spec("Name", "0", "Reset", "Reset name options", "reset_rules"),
    _spec("Edit", "i", "Edit", "Edit this name", "insert"),
    _spec("Edit", "l", "Lock", "Lock this file", "lock"),
    _spec("Edit", "L", "Lock all", "Lock selected files", "lock_selected"),
    _spec("Edit", "U", "Unlock", "Unlock all", "unlock_all"),
    _spec("Actions", "enter", "Apply", "Apply the current mode", "apply"),
    _spec("Actions", "D", "Filename date", "Set dates from filenames", "date_mode", "date_mode"),
    _spec("Actions", "W", "Write zone", "Write date and timezone offset", "write_timezone"),
    _spec("Actions", "M", "mtime", "Set mtime mode", "mtime_mode", "mtime_mode"),
    _spec("Actions", "B", "Write GPS", "Write JSON location into EXIF", "write_geodata"),
    _spec("Actions", "T", "Timezone", "Choose a timezone", "timezone"),
    _spec("Actions", "u", "Undo", "Undo the last batch", "undo"),
    _spec("Actions", "P", "Preview", "Show or hide the preview", "preview", "preview_on"),
    _spec("Actions", "r", "Refresh", "Reload the folder", "reload_files"),
    _spec("Actions", "?", "Help", "Show commands", "help"),
    _spec("Actions", "q", "Quit", "Quit", "quit"),
)

FILE_ACTIONS = {
    "apply",
    "insert",
    "lock",
    "lock_selected",
    "date_mode",
    "mtime_mode",
    "write_timezone",
    "write_geodata",
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
