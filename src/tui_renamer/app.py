"""Textual UI for rn."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, tzinfo
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Click
from textual.geometry import Size
from textual.layouts.grid import GridLayout
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from tui_renamer import preview as file_preview
from tui_renamer.commands import (
    COMMAND_SPECS,
    CommandContext,
    CommandSpec,
    matching_commands,
)
from tui_renamer.core import (
    ZONEINFO_AVAILABLE,
    DatePlanItem,
    FileFacts,
    RenameRules,
    date_source,
    datetime_for_write,
    delta_text,
    filter_timezones,
    fit_middle,
    flag_letter,
    format_offset,
    format_tz_display,
    is_warning,
    list_files,
    list_timezones,
    looks_like_timezone,
    missing_marks,
    mtime_source,
    name_diff_ops,
    neighbor_flags,
    parse_filename_datetime,
    plan_filename_dates,
    preview_name,
    rename_paths,
    reset_name_rules,
    resolve_tz,
    show_not_oldest_warning,
    timeline_bins,
)
from tui_renamer.journal import Batch, Journal, RenameOp
from tui_renamer.metadata import (
    IMAGE_EXTENSIONS,
    PIEXIF_AVAILABLE,
    WriteCounts,
    can_write_metadata,
    find_exiftool,
    find_setfile,
    inspect_file,
    set_birthtime,
    set_filesystem_times,
    tally_write,
    timezone_write_message,
    write_file_metadata_and_offset,
    write_geodata,
)
from tui_renamer.search import (
    find_label,
    first_at_or_after,
    highlighted_name,
    kept_span,
    match_span,
    query_hits,
)

TOGGLE_FIELDS = {
    "d": "add_datetime",
    "m": "add_modtime",
    "o": "add_origtime",
    "e": "use_json_data",
    "b": "add_geodata",
    "t": "use_prefix",
    "x": "strip_original_name",
    "y": "strip_to_text",
    "c": "remove_caps",
    "s": "remove_special",
    "p": "replace_spaces",
    ".": "replace_dots",
}

TOGGLE_TIPS = {
    "add_datetime": "Add today's date and time",
    "add_modtime": "Add the modified time",
    "add_origtime": "Add the original time",
    "use_json_data": "Read a sidecar JSON file",
    "add_geodata": "Add JSON location to the name",
    "use_prefix": "Times at the start when on",
    "strip_original_name": "Drop the original name",
    "strip_to_text": "Keep letters and spaces",
    "remove_caps": "Lowercase the name",
    "remove_special": "Remove symbols",
    "replace_spaces": "Spaces become dashes",
    "replace_dots": "Dots to dashes",
    "date_mode": "Set dates from filenames",
    "mtime_mode": "Set mtime mode",
    "preview": "Show or hide the preview",
}

BUTTON_TIPS = {
    "act-apply": "Apply the current mode",
    "act-undo": "Undo the last batch",
    "act-zone": "Choose a timezone",
    "act-write-zone": "Write date and timezone offset",
    "act-gps": "Write JSON location into EXIF",
    "act-refresh": "Reload the folder",
    "act-reset": "Reset name options",
}


OPTION_FIELDS = (
    ("d", "add_datetime", "Date"),
    ("m", "add_modtime", "Mod"),
    ("o", "add_origtime", "Orig"),
    ("e", "use_json_data", "JSON"),
    ("b", "add_geodata", "Geo"),
    ("t", "use_prefix", "Prefix"),
    ("x", "strip_original_name", "Strip"),
    ("y", "strip_to_text", "Text"),
    ("c", "remove_caps", "lower"),
    ("s", "remove_special", "Sym"),
    ("p", "replace_spaces", "Dash"),
    (".", "replace_dots", "Dot"),
)


def linux_stdin_deleted() -> bool:
    """True when Linux reports that the terminal device for stdin is gone."""
    if sys.platform != "linux":
        return False
    try:
        fd = sys.__stdin__.fileno()
        if not os.isatty(fd):
            return False
        target = os.readlink(f"/proc/self/fd/{fd}")
    except (OSError, ValueError):
        return False
    return target.endswith(" (deleted)")


def split_stem(filename: str) -> tuple[str, str]:
    """Split a name into the editable stem and the extension."""
    dot = filename.rfind(".")
    if 0 < dot < len(filename) - 1:
        return filename[:dot], filename[dot:]
    return filename, ""


def _invoke(app: RenamerApp, spec: CommandSpec) -> None:
    run = spec.run
    if run.startswith("toggle:"):
        app.action_toggle(run.split(":", 1)[1])
    else:
        getattr(app, f"action_{run}")()


class DebugLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is None:
            return
        try:
            path.write_text(
                f"--- rn debug ---\nSession started: {datetime.now().isoformat()}\n\n",
                encoding="utf-8",
            )
        except OSError:
            self.path = None

    def write(self, message: str) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except OSError:
            pass


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", show=False),
        Binding("n,escape", "answer(False)", show=False),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="confirm-question")
            yield Button("Yes", id="yes")
            yield Button("No", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss(True)
        elif event.button.id == "no":
            self.dismiss(False)


class CommandFilter(Input):
    """Filter box that leaves ? for the screen binding."""

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key == "question_mark":
            return False
        return super().check_consume_key(key, character)


class CommandScreen(ModalScreen[str | None]):
    """One list for help and for running a command. ? closes from the filter."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("question_mark", "close", show=False, priority=True),
        Binding("up", "prev", show=False, priority=True),
        Binding("down", "next", show=False, priority=True),
        Binding("enter", "pick", show=False, priority=True),
    ]

    def __init__(self, ctx: CommandContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.shown: list[tuple[CommandSpec, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Commands", markup=False)
            yield CommandFilter(placeholder="Filter", id="cmd-filter")
            yield OptionList(id="cmd-list")
            yield Static("? or Esc closes. Enter runs the highlighted command.", id="cmd-hint")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one("#cmd-filter", CommandFilter).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cmd-filter":
            self._refill(event.value)

    def _refill(self, query: str) -> None:
        self.shown = matching_commands(query, self.ctx)
        options = self.query_one("#cmd-list", OptionList)
        options.clear_options()
        for spec, reason in self.shown:
            label = f"{spec.key}  {spec.label}  {spec.description}"
            if reason:
                prompt = Text.assemble(label, "  ", (reason, "dim"))
            else:
                prompt = Text(label)
            options.add_option(Option(prompt, disabled=bool(reason)))
        options.highlighted = None
        if any(not reason for _spec, reason in self.shown):
            options.action_cursor_down()

    def action_prev(self) -> None:
        self.query_one("#cmd-list", OptionList).action_cursor_up()

    def action_next(self) -> None:
        self.query_one("#cmd-list", OptionList).action_cursor_down()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self) -> None:
        options = self.query_one("#cmd-list", OptionList)
        if options.highlighted is None:
            return
        spec, reason = self.shown[options.highlighted]
        if reason:
            return
        self.dismiss(spec.run)


class InsertScreen(ModalScreen[tuple[str, str]]):
    """Edit the stem. Escape saves. Enter saves and renames."""

    BINDINGS = [Binding("escape", "save", show=False)]

    def __init__(self, stem: str, extension: str) -> None:
        super().__init__()
        self.stem = stem
        self.extension = extension

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Edit name", markup=False)
            yield Input(value=self.stem, id="stem", select_on_focus=True)
            yield Static(self.extension or "(no extension)", id="extension", markup=False)
            yield Static("Esc saves. Enter saves and renames.", markup=False)

    def on_mount(self) -> None:
        self.query_one("#stem", Input).focus()

    def _full(self) -> str:
        return self.query_one("#stem", Input).value + self.extension

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(("rename", event.value + self.extension))

    def action_save(self) -> None:
        self.dismiss(("save", self._full()))


class TimezoneScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("up", "prev", show=False),
        Binding("down", "next", show=False),
        Binding("ctrl+u", "clear_query", show=False),
    ]

    def __init__(self, current: str | None) -> None:
        super().__init__()
        self.names = list_timezones()
        self.current = current or "Local System"
        self.matches = list(self.names)
        self.index = self.names.index(self.current) if self.current in self.names else 0

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Select timezone", markup=False)
            yield Input(placeholder="Search", id="query")
            yield OptionList(id="zones", markup=False)
            yield Static("", id="tz-hint", markup=False)

    def on_mount(self) -> None:
        self._refill()
        self.query_one("#query", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.matches = filter_timezones(self.names, event.value)
        self.index = 0
        self._refill()

    def _refill(self) -> None:
        options = self.query_one("#zones", OptionList)
        options.clear_options()
        now = datetime.now()
        for name in self.matches:
            options.add_option(format_tz_display(name, now))
        hint = f"{len(self.matches)} matches"
        if self.matches:
            self.index = min(self.index, len(self.matches) - 1)
            options.highlighted = self.index
        else:
            hint = "No matching timezones"
        self.query_one("#tz-hint", Static).update(hint)

    def action_prev(self) -> None:
        if not self.matches:
            return
        self.index = max(0, self.index - 1)
        self.query_one("#zones", OptionList).highlighted = self.index

    def action_next(self) -> None:
        if not self.matches:
            return
        self.index = min(len(self.matches) - 1, self.index + 1)
        self.query_one("#zones", OptionList).highlighted = self.index

    def action_pick(self) -> None:
        if self.matches:
            self.dismiss(self.matches[self.index])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_clear_query(self) -> None:
        self.query_one("#query", Input).value = ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        del event
        self.action_pick()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.index = event.option_index
        self.action_pick()


class FileTable(DataTable):
    BINDINGS = [
        Binding("enter", "app.apply", show=False),
        Binding("space", "app.toggle_select", show=False),
        Binding("j", "app.cursor_down", show=False),
        Binding("k", "app.cursor_up", show=False),
        Binding("up", "app.cursor_up", show=False),
        Binding("down", "app.cursor_down", show=False),
        Binding("pageup", "app.page_up", show=False),
        Binding("pagedown", "app.page_down", show=False),
        Binding("g", "app.leader_g", show=False),
        Binding("G", "app.goto_end", show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)  # type: ignore[arg-type]


class PreviewSlot(Vertical):
    can_focus = True


class DetailPane(Vertical):
    can_focus = True

    def compose(self) -> ComposeResult:
        with PreviewSlot(id="preview-slot"):
            yield Static("", id="preview-caption")
            yield Static("", id="preview-fallback")
        with VerticalScroll(id="detail-scroll"):
            yield Static("", id="detail-body")


class OptionToggle(Static):
    """One-line chip, such as ` d Date `. A click assigns the other state."""

    can_focus = True

    class Changed(Message):
        def __init__(self, toggle: OptionToggle, value: bool) -> None:
            super().__init__()
            self.toggle = toggle
            self.value = value

    def __init__(
        self,
        key: str,
        caption: str,
        value: bool = False,
        *,
        tooltip: str = "",
        id: str | None = None,
    ) -> None:
        self.key_name = key
        self.caption = caption
        self._value = value
        super().__init__(self._chip(), id=id, markup=False)
        if value:
            self.add_class("-on")
        if tooltip:
            self.tooltip = tooltip

    @property
    def value(self) -> bool:
        return self._value

    def _chip(self) -> Text:
        text = Text()
        text.append(" ")
        text.append(self.key_name, style="bold underline")
        text.append(f" {self.caption} ")
        return text

    def set_value(self, value: bool) -> None:
        """Show `value` without posting Changed, so a refresh cannot toggle twice."""
        self._value = value
        self.set_class(value, "-on")
        self.update(self._chip())

    def get_content_width(self, container: Size, viewport: Size) -> int:
        del container, viewport
        return 1 + len(self.key_name) + 1 + len(self.caption) + 1

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        del container, viewport, width
        return 1

    def on_click(self, event: Click) -> None:
        event.stop()
        self.set_value(not self._value)
        self.post_message(self.Changed(self, self._value))


class WrappingRow(Widget):
    """Grid row. Children stay one line tall and wrap instead of being clipped."""

    def pre_layout(self, layout: object) -> None:
        if not isinstance(layout, GridLayout) or not self.children:
            return
        viewport = self.app.size
        widest = 1
        for child in self.children:
            widest = max(widest, child.get_content_width(self.size, viewport))
        layout.min_column_width = widest


class OptionsPane(Vertical):
    """One-line toggles and buttons for the rules that used to be chips."""

    can_focus = True

    def on_mount(self) -> None:
        for button in self.query(Button):
            button.styles.line_pad = 0

    def compose(self) -> ComposeResult:
        with WrappingRow(id="opt-toggles"):
            for key, field, label in OPTION_FIELDS:
                yield OptionToggle(key, label, tooltip=TOGGLE_TIPS[field], id=f"opt-{field}")
        with WrappingRow(id="opt-modes"):
            yield OptionToggle(
                "D", "Filename date", tooltip=TOGGLE_TIPS["date_mode"], id="opt-date_mode"
            )
            yield OptionToggle("M", "mtime", tooltip=TOGGLE_TIPS["mtime_mode"], id="opt-mtime_mode")
            yield OptionToggle(
                "P",
                "Preview",
                value=True,
                tooltip=TOGGLE_TIPS["preview"],
                id="opt-preview",
            )
        with WrappingRow(id="opt-actions"):
            yield Button(
                "Enter Apply", id="act-apply", compact=True, tooltip=BUTTON_TIPS["act-apply"]
            )
            yield Button("u Undo", id="act-undo", compact=True, tooltip=BUTTON_TIPS["act-undo"])
            yield Button("T Local", id="act-zone", compact=True, tooltip=BUTTON_TIPS["act-zone"])
            yield Button(
                "W Write zone",
                id="act-write-zone",
                compact=True,
                tooltip=BUTTON_TIPS["act-write-zone"],
            )
            yield Button("B GPS", id="act-gps", compact=True, tooltip=BUTTON_TIPS["act-gps"])
            yield Button(
                "r Refresh", id="act-refresh", compact=True, tooltip=BUTTON_TIPS["act-refresh"]
            )
            yield Button("0 Reset", id="act-reset", compact=True, tooltip=BUTTON_TIPS["act-reset"])


SOURCE_STYLE = {
    "E": "green",
    "J": "magenta",
    "B": "blue",
    "M": "yellow",
    "F": "cyan",
}
FLAG_STYLE = {
    "H": "bold yellow",
    "Y": "red",
    "G": "yellow",
    "!": "bold red",
}
STATUS_SHORT = {
    "UNCHANGED": "---",
    "MODIFIED": "MOD",
    "SELECTED": "SEL",
    "MANUAL": "MAN",
    "LOCKED": "LCK",
    "JSON ERR": "ERR",
    "NO DATE": "NOD",
    "UPDATE": "UPD",
    "ERR": "ERR",
}
SOURCE_NAME = {
    "E": "EXIF",
    "J": "JSON sidecar",
    "B": "birth or ctime",
    "M": "mtime",
    "F": "filename",
}
MISSING_NAME = {
    "e": "no EXIF date",
    "z": "no timezone offset",
    "g": "no GPS",
    "j": "no sidecar",
    "J": "sidecar could not be read",
}
FLAG_NAME = {
    "H": "looks like a timezone offset",
    "Y": "year outside 1990 through next year",
    "G": "far from the files on either side",
    "!": "chosen time is not the oldest",
}


class RenamerApp(App[None]):
    """Batch renamer. Keys match the original curses script."""

    TITLE = "rn"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #files { width: 1fr; height: 100%; }
    #detail { width: 1fr; height: 100%; }
    #options { height: auto; padding: 0 1; }
    #opt-toggles, #opt-modes, #opt-actions {
        layout: grid;
        height: auto;
        width: 100%;
        grid-gutter: 0 1;
    }
    #options OptionToggle, #options Button {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: auto;
        min-width: 0;
        margin: 0;
        padding: 0;
        border: none;
        background: $surface;
        color: $text-muted;
        content-align: left middle;
        pointer: pointer;
    }
    #options OptionToggle.-on {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #options OptionToggle:hover, #options Button:hover {
        background: $primary;
        color: $text;
        height: 1;
        border: none;
    }
    #options OptionToggle.-on:hover {
        background: $accent-lighten-1;
        color: $text;
        text-style: bold;
    }
    #options OptionToggle:focus, #options Button:focus {
        background: $primary-darken-1;
        color: $text;
        text-style: bold;
        height: 1;
        border: none;
    }
    #timeline { height: 1; padding: 0 1; }
    #status { height: auto; max-height: 3; padding: 0 1; }
    #find { display: none; height: 1; margin: 0 1; }
    #find.-open { display: block; }
    #detail { layout: vertical; }
    #preview-slot { height: auto; max-height: 12; overflow: hidden; }
    #preview-slot.-off { display: none; }
    #preview-caption { height: auto; max-height: 2; }
    #preview-fallback { height: auto; max-height: 8; }
    #preview-image { height: 8; width: 100%; }
    #detail-scroll { height: 1fr; }
    #files, #detail, #options, #preview-slot { border: solid $primary; }
    .-zoom-files #detail { display: none; }
    .-zoom-detail #files { display: none; }
    .-zoom-options #body { display: none; }
    .-zoom-options #options { height: 1fr; }
    .-zoom-preview #files { display: none; }
    .-zoom-preview #options { display: none; }
    .-zoom-preview #detail-scroll { display: none; }
    .-zoom-preview #preview-slot { height: 1fr; max-height: 100%; }
    .-zoom-preview #preview-image { height: 1fr; }
    ConfirmScreen, CommandScreen, TimezoneScreen, InsertScreen { align: center middle; }
    #dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    CommandScreen #dialog { width: 84; height: 24; }
    #cmd-list { height: 1fr; }
    #zones { height: 12; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "help", "Commands"),
        Binding("h", "help", show=False),
        Binding("f1", "help", show=False),
        Binding("ctrl+p", "help", show=False),
        Binding(":", "help", "Commands"),
        Binding("space", "toggle_select", "Select"),
        Binding("enter", "apply", "Apply"),
        Binding("escape", "escape", show=False),
        Binding("1", "focus_files", show=False),
        Binding("2", "focus_detail", show=False),
        Binding("3", "focus_rules", show=False),
        Binding("4", "focus_preview", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("g", "leader_g", show=False),
        Binding("G", "goto_end", show=False),
        Binding("slash", "find", show=False),
        Binding("n", "next_change", show=False),
        Binding("N", "prev_change", show=False),
        Binding("right_square_bracket", "next_changed", show=False),
        Binding("left_square_bracket", "prev_changed", show=False),
        Binding("ctrl+g", "search_again", show=False),
        Binding("!", "next_warning", show=False),
        Binding("P", "preview", show=False),
        Binding("v", "visual", show=False),
        Binding("a", "select_all", show=False),
        Binding("A", "select_none", show=False),
        Binding("d", "toggle('add_datetime')", show=False),
        Binding("m", "toggle('add_modtime')", show=False),
        Binding("o", "toggle('add_origtime')", show=False),
        Binding("e", "toggle('use_json_data')", show=False),
        Binding("b", "toggle('add_geodata')", show=False),
        Binding("t", "toggle('use_prefix')", show=False),
        Binding("x", "toggle('strip_original_name')", show=False),
        Binding("y", "toggle('strip_to_text')", show=False),
        Binding("c", "toggle('remove_caps')", show=False),
        Binding("s", "toggle('remove_special')", show=False),
        Binding("p", "toggle('replace_spaces')", show=False),
        Binding(".", "toggle('replace_dots')", show=False),
        Binding("0", "reset_rules", show=False),
        Binding("i", "insert", show=False),
        Binding("l", "lock", show=False),
        Binding("L", "lock_selected", show=False),
        Binding("U", "unlock_all", show=False),
        Binding("D", "date_mode", show=False),
        Binding("W", "write_timezone", show=False),
        Binding("M", "mtime_mode", show=False),
        Binding("B", "write_geodata", show=False),
        Binding("T", "timezone", show=False),
        Binding("u", "undo", show=False),
        Binding("r", "reload_files", show=False),
    ]

    def __init__(self, directory: Path, debug: bool = False) -> None:
        super().__init__()
        self.directory = directory
        self.rules = RenameRules()
        self.files: list[Path] = []
        self.selected: set[int] = set()
        self.locked: dict[int, str] = {}
        self.manual: dict[int, str] = {}
        self._facts: dict[tuple[str, str | None, bool], FileFacts] = {}
        self._cursor = 0
        self.visual = False
        self.anchor: int | None = None
        self.date_mode = False
        self.mtime_mode = False
        self._leader = False
        self._message = ""
        self._rebuilding = False
        self._changing: set[int] = set()
        self._neighbors: list[str] = []
        self.exiftool = find_exiftool()
        self.setfile = find_setfile()
        self.journal = Journal(Path(tempfile.mkdtemp(prefix="rn-undo-")))
        log_path = directory / "renamer_debug.log" if debug else None
        self.debug_log = DebugLog(log_path)
        self.preview_on = True
        self._thumbs = file_preview.ThumbCache()
        self._preview_gen = 0
        self._preview_timer = None
        self._find_open = False
        self._find_origin = 0
        self._draft = ""
        self._search_query = ""
        self._search_active = False
        self._search_nav = False
        self._name_width = 8
        self._new_width = 8
        self._zoom: str | None = None
        self._syncing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="timeline")
        with Horizontal(id="body"):
            yield FileTable(id="files")
            yield DetailPane(id="detail")
        yield OptionsPane(id="options")
        yield Input(placeholder="find", id="find", select_on_focus=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#files").border_title = "1 Files"
        self.query_one("#detail").border_title = "2 Detail"
        self.query_one("#options").border_title = "3 Options"
        self.query_one("#preview-slot").border_title = "4 Preview"
        self.rules.datetime_stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.reload_files()
        if not self.exiftool:
            self.set_status(
                "exiftool is not on PATH. Video, HEIC, and PNG metadata writes are off."
            )
        self.refresh_view()
        self._apply_layout(self.size.width)
        self.set_interval(0.5, self._tick)
        self.query_one(FileTable).focus()

    def on_unmount(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self.journal.cleanup()

    def on_resize(self, event: object) -> None:
        width = getattr(getattr(event, "size", None), "width", self.size.width)
        if self.is_mounted and not self._rebuilding:
            self._apply_layout(width)
            self.refresh_view()

    def _apply_layout(self, width: int) -> None:
        body = self.query_one("#body")
        files = self.query_one("#files")
        detail = self.query_one("#detail")
        narrow = width < 100
        body.set_class(narrow, "-narrow")
        if narrow:
            body.styles.layout = "vertical"
            files.styles.width = "100%"
            detail.styles.width = "100%"
            files.styles.height = "1fr"
            detail.styles.height = "1fr"
        else:
            body.styles.layout = "horizontal"
            files.styles.width = "1fr"
            detail.styles.width = "1fr"
            files.styles.height = "100%"
            detail.styles.height = "100%"

    def _tick(self) -> None:
        if linux_stdin_deleted():
            self.exit()
            return
        if not self.rules.add_datetime or len(self.screen_stack) > 1:
            return
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        if stamp != self.rules.datetime_stamp:
            self.rules.datetime_stamp = stamp
            self.refresh_view()

    def debug(self, message: str) -> None:
        self.debug_log.write(message)

    def set_status(self, message: str) -> None:
        self._message = message

    def _begin(self) -> None:
        self._leader = False
        self._message = ""
        self._search_nav = False

    def reload_files(self) -> None:
        self.files = list_files(self.directory)
        self.selected.clear()
        self.locked.clear()
        self.manual.clear()
        self._facts.clear()
        self._cursor = 0
        self.visual = False
        self.anchor = None

    def target_zone(self) -> tzinfo:
        return resolve_tz(self.rules.source_tz)

    def plan_zone(self) -> tzinfo | None:
        if self.rules.source_tz:
            return resolve_tz(self.rules.source_tz)
        return None

    def facts_for(self, path: Path) -> FileFacts:
        key = (str(path), self.rules.source_tz, self.rules.use_json_data)
        cached = self._facts.get(key)
        if cached is None:
            cached = inspect_file(path, self.rules, self.target_zone())
            self._facts[key] = cached
        return cached

    def preview_for(self, index: int) -> str:
        path = self.files[index]
        if index in self.locked:
            return self.locked[index]
        if index in self.manual:
            return self.manual[index]
        return preview_name(path.name, self.facts_for(path), self.rules)

    def refresh_view(self) -> None:
        self._rebuilding = True
        try:
            self._recompute_changing()
            self._recompute_neighbors()
            self._fill_table()
            self._sync_options()
            self._update_detail()
            self._update_timeline()
            self._update_status()
            self._update_subtitle()
        finally:
            self._rebuilding = False

    def _recompute_changing(self) -> None:
        """Indexes whose apply preview differs from the file as it is now."""
        self._changing = set()
        if not self.files:
            return
        if self.date_mode:
            plan = plan_filename_dates(self.files, self.plan_zone())
            todo = {item.path for item in plan.todo}
            for index, path in enumerate(self.files):
                if path not in todo:
                    continue
                if self.selected and index not in self.selected:
                    continue
                self._changing.add(index)
            return
        if self.mtime_mode:
            local = _local_zone()
            for index, path in enumerate(self.files):
                if index not in self.selected:
                    continue
                _label, moment = mtime_source(self.facts_for(path), self.rules, local)
                if moment is not None:
                    self._changing.add(index)
            return
        for index, path in enumerate(self.files):
            if self.preview_for(index) != path.name:
                self._changing.add(index)

    def _recompute_neighbors(self) -> None:
        instants = [self.facts_for(path).winner_instant for path in self.files]
        self._neighbors = neighbor_flags(instants, datetime.now(self.target_zone()))

    def _neighbor_at(self, index: int) -> str:
        if 0 <= index < len(self._neighbors):
            return self._neighbors[index]
        return ""

    def _mark_cells(self, index: int, path: Path) -> tuple[Text, Text, Text]:
        facts = self.facts_for(path)
        letter = date_source(facts)
        source = Text(letter, style=SOURCE_STYLE.get(letter, ""))
        flag = flag_letter(facts, self._neighbor_at(index))
        flag_text = Text(flag, style=FLAG_STYLE.get(flag, ""))
        marks = missing_marks(
            is_image=path.suffix.lower() in IMAGE_EXTENSIONS,
            has_exif=facts.has_exif,
            has_offset=facts.has_offset,
            has_gps=facts.has_gps,
            has_sidecar_geo=bool(facts.geodata_raw),
            json_mode=self.rules.use_json_data,
            sidecar_found=facts.sidecar is not None,
            json_error=facts.json_error,
        )
        missing = Text()
        for char in marks:
            missing.append(char, style="red" if char == "J" else "dim")
        if len(marks) < 4:
            missing.append(" " * (4 - len(marks)))
        return source, flag_text, missing

    def _short_status(self, status: str) -> str:
        return STATUS_SHORT.get(status, status[:3])

    def _status_code(self, index: int) -> str:
        path = self.files[index]
        if self.date_mode:
            plan = plan_filename_dates(self.files, self.plan_zone())
            by_path = {item.path: item for item in plan.todo + plan.same}
            item = by_path.get(path)
            if item is None:
                parsed, _precision = parse_filename_datetime(path.name)
                status = "NO DATE" if parsed is None else "ERR"
            else:
                status = "UPDATE" if item in plan.todo else "UNCHANGED"
            if index in self.selected:
                status = "SELECTED"
            return status
        if self.mtime_mode:
            return "SELECTED" if index in self.selected else "UNCHANGED"
        return self._rename_status(index, self.preview_for(index))

    def _mark_sentence(self) -> str:
        path = self.files[self._cursor]
        facts = self.facts_for(path)
        source = SOURCE_NAME.get(facts.winner_source, "none")
        flag = flag_letter(facts, self._neighbor_at(self._cursor))
        flag_name = FLAG_NAME.get(flag, "none")
        marks = missing_marks(
            is_image=path.suffix.lower() in IMAGE_EXTENSIONS,
            has_exif=facts.has_exif,
            has_offset=facts.has_offset,
            has_gps=facts.has_gps,
            has_sidecar_geo=bool(facts.geodata_raw),
            json_mode=self.rules.use_json_data,
            sidecar_found=facts.sidecar is not None,
            json_error=facts.json_error,
        )
        if marks:
            missing = ", ".join(MISSING_NAME.get(char, char) for char in marks)
        else:
            missing = "none"
        stat = self._short_status(self._status_code(self._cursor))
        return f"Src {source}. Flag {flag_name}. Miss {missing}. Stat {stat}."

    def _update_timeline(self) -> None:
        widget = self.query_one("#timeline", Static)
        width = widget.size.width or self.size.width
        if width <= 0:
            width = 80
        instants = [self.facts_for(path).winner_instant for path in self.files]
        highlight = self._cursor if self.files else None
        line = timeline_bins(instants, width, highlight)
        if not line:
            widget.update(Text("no dates", style="dim"))
        else:
            widget.update(line)

    def _flex_width(self, fixed_content: int, column_count: int, flex_count: int) -> int:
        """Content width for each flexible column so the row fits the pane."""
        table = self.query_one(FileTable)
        viewport = table.scrollable_content_region.width
        if viewport <= 0:
            viewport = max(table.size.width - 2, 64)
        pad = 2 * table.cell_padding * column_count
        remaining = viewport - fixed_content - pad
        if flex_count <= 0:
            return 8
        return max(8, remaining // flex_count)

    def _styled(
        self,
        value: str,
        width: int,
        style: str,
        span: tuple[int, int] | None = None,
    ) -> Text:
        shown = fit_middle(value, width)
        text = Text(shown, style=style)
        if span is not None:
            mapped = kept_span(value, width, span[0], span[1])
            if mapped is not None:
                text.stylize("reverse", mapped[0], mapped[1])
        return text

    def _match_query(self) -> str:
        if self._find_open:
            return self._draft
        if self._search_active:
            return self._search_query
        return ""

    def _name_spans(
        self, current: str, new: str
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        query = self._match_query()
        if not query:
            return None, None
        return match_span(current, query), match_span(new, query)

    def _hit_indexes(self, query: str | None = None) -> list[int]:
        needle = self._search_query if query is None else query
        names = [(path.name, self.preview_for(index)) for index, path in enumerate(self.files)]
        return query_hits(names, needle)

    def _fill_table(self) -> None:
        table = self.query_one(FileTable)
        table.clear(columns=True)
        if self.date_mode:
            self._add_date_columns(table)
            rows = self._date_rows()
        elif self.mtime_mode:
            self._add_mtime_columns(table)
            rows = self._mtime_rows()
        else:
            self._add_rename_columns(table)
            rows = self._rename_rows()
        for row in rows:
            table.add_row(*row)
        if table.row_count:
            self._cursor = min(self._cursor, table.row_count - 1)
            table.move_cursor(row=self._cursor)
        else:
            self._cursor = 0

    def _add_rename_columns(self, table: FileTable) -> None:
        width = self._flex_width(fixed_content=18, column_count=7, flex_count=2)
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Src", key="src", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("Name", key="name", width=width)
        table.add_column("New", key="new", width=width)
        table.add_column("Stat", key="status", width=4)
        self._name_width = width
        self._new_width = width

    def _add_date_columns(self, table: FileTable) -> None:
        width = self._flex_width(fixed_content=56, column_count=8, flex_count=1)
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Src", key="src", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("File", key="file", width=width)
        table.add_column("Current", key="cur", width=19)
        table.add_column("New", key="new", width=19)
        table.add_column("Stat", key="status", width=4)
        self._name_width = width
        self._new_width = 19

    def _add_mtime_columns(self, table: FileTable) -> None:
        width = self._flex_width(fixed_content=37, column_count=7, flex_count=1)
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Src", key="src", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("Name", key="name", width=width)
        table.add_column("New mtime", key="mtime", width=19)
        table.add_column("Stat", key="status", width=4)
        self._name_width = width
        self._new_width = 19

    def _mark(self, index: int) -> str:
        return "*" if index in self.selected else ""

    def _rename_rows(self) -> list[tuple[str, Text, Text, Text, Text, Text, str]]:
        rows: list[tuple[str, Text, Text, Text, Text, Text, str]] = []
        name_w = self._name_width
        new_w = self._new_width
        for index, path in enumerate(self.files):
            new_name = self.preview_for(index)
            name_span, new_span = self._name_spans(path.name, new_name)
            changing = index in self._changing
            if index in self.locked:
                new_style = "bold green"
            elif index in self.manual:
                new_style = "bold yellow"
            elif changing:
                new_style = "yellow"
            else:
                new_style = "dim"
            name_style = "" if changing else "dim"
            source, flag, missing = self._mark_cells(index, path)
            rows.append(
                (
                    self._mark(index),
                    source,
                    flag,
                    missing,
                    self._styled(path.name, name_w, name_style, name_span),
                    self._styled(new_name, new_w, new_style, new_span),
                    self._short_status(self._rename_status(index, new_name)),
                )
            )
        return rows

    def _rename_status(self, index: int, new_name: str) -> str:
        facts = self.facts_for(self.files[index])
        if index in self.locked:
            return "LOCKED"
        if index in self.manual:
            return "MANUAL"
        if index in self.selected:
            return "SELECTED"
        if facts.json_error:
            return "JSON ERR"
        if new_name != self.files[index].name:
            return "MODIFIED"
        return "UNCHANGED"

    def _date_rows(self) -> list[tuple[str, Text, Text, Text, Text, str, Text, str]]:
        plan = plan_filename_dates(self.files, self.plan_zone())
        by_path = {item.path: item for item in plan.todo + plan.same}
        zone = self.plan_zone()
        rows: list[tuple[str, Text, Text, Text, Text, str, Text, str]] = []
        for index, path in enumerate(self.files):
            item = by_path.get(path)
            current = self._clock(path, zone)
            if item is None:
                parsed, _precision = parse_filename_datetime(path.name)
                new = "---" if parsed is None else "Error"
                status = "NO DATE" if parsed is None else "ERR"
            else:
                new = _format_stamp(item.target, zone)
                status = "UPDATE" if item in plan.todo else "UNCHANGED"
            if index in self.selected:
                status = "SELECTED"
            file_style = "" if index in self._changing else "dim"
            new_style = "" if index in self._changing else "dim"
            name_span, _new_span = self._name_spans(path.name, self.preview_for(index))
            source, flag, missing = self._mark_cells(index, path)
            rows.append(
                (
                    self._mark(index),
                    source,
                    flag,
                    missing,
                    self._styled(path.name, self._name_width, file_style, name_span),
                    current,
                    self._styled(new, self._new_width, new_style),
                    self._short_status(status),
                )
            )
        return rows

    def _mtime_rows(self) -> list[tuple[str, Text, Text, Text, Text, str, str]]:
        local = _local_zone()
        rows: list[tuple[str, Text, Text, Text, Text, str, str]] = []
        for index, path in enumerate(self.files):
            label, _moment = mtime_source(self.facts_for(path), self.rules, local)
            status = "SELECTED" if index in self.selected else "UNCHANGED"
            name_style = "" if index in self._changing else "dim"
            name_span, _new_span = self._name_spans(path.name, self.preview_for(index))
            source, flag, missing = self._mark_cells(index, path)
            rows.append(
                (
                    self._mark(index),
                    source,
                    flag,
                    missing,
                    self._styled(path.name, self._name_width, name_style, name_span),
                    label,
                    self._short_status(status),
                )
            )
        return rows

    def _clock(self, path: Path, zone: tzinfo | None) -> str:
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return "Error"
        return _format_stamp(stamp, zone)

    def _update_detail(self) -> None:
        self.query_one("#detail-body", Static).update(self._detail_text())
        self._schedule_preview()

    def _detail_text(self) -> Text:
        text = Text()
        if not self.files:
            text.append("No files in this folder.\n")
            text.append("Press r after you add some.\n", style="dim")
            return text
        path = self.files[self._cursor]
        facts = self.facts_for(path)
        new_name = self.preview_for(self._cursor)
        text.append(path.name + "\n", style="bold")
        text.append(self._mark_sentence() + "\n")
        if self._cursor in self.locked:
            text.append("locked\n", style="green")
        elif self._cursor in self.manual:
            text.append("manual edit\n", style="yellow")
        for tag, piece in name_diff_ops(path.name, new_name):
            style = {"equal": "", "delete": "red strike", "insert": "green"}[tag]
            text.append(piece, style=style)
        text.append("\n\n")
        if facts.json_error:
            text.append("JSON sidecar could not be read.\n", style="red")
        if facts.sidecar is not None:
            text.append(f"sidecar  {facts.sidecar.name}\n")
        text.append("Dates\n", style="bold")
        if facts.candidates:
            for candidate in facts.candidates:
                text.append(f"  {candidate.label}\n    {candidate.text}\n")
        else:
            text.append("  none\n", style="dim")
        text.append("\nWinner\n", style="bold")
        source_name = SOURCE_NAME.get(facts.winner_source, "none")
        text.append(f"  original  {facts.origtime_text or 'none'}\n")
        text.append(f"  modified  {facts.modtime_text or 'none'}\n")
        text.append(f"  source  {source_name}\n")
        self._append_deltas(text, facts, path)
        neighbor = self._neighbor_at(self._cursor)
        if neighbor == "Y":
            text.append("This year is outside 1990 through next year.\n", style="red")
        elif neighbor == "G":
            text.append("This date sits far from the files on either side.\n", style="yellow")
        if looks_like_timezone(facts):
            text.append("Looks like a timezone offset.\n", style="bold yellow")
        zone_name = self.rules.source_tz or "Local"
        offset = format_offset(datetime.now(self.target_zone()))
        text.append(f"\nZone  {zone_name} ({offset})\n")
        if facts.geodata:
            text.append(f"Geodata  {facts.geodata}\n")
            if facts.geodata_raw:
                text.append(
                    f"  {facts.geodata_raw['latitude']}, {facts.geodata_raw['longitude']}\n"
                )
        else:
            text.append("Geodata  none\n", style="dim")
        if show_not_oldest_warning(facts, self.rules):
            text.append("\n! Chosen time is not the oldest\n", style="bold yellow")
        return text

    def _append_deltas(self, text: Text, facts: FileFacts, path: Path) -> None:
        text.append("\nDeltas\n", style="bold")
        wrote = False
        for candidate in facts.candidates:
            if candidate.label == "Geodata":
                continue
            delta = delta_text(facts.winner_instant, candidate)
            suffix = f"  {delta}" if delta else ""
            text.append(f"  {candidate.label}\n    {candidate.text}{suffix}\n")
            wrote = True
        if not wrote:
            text.append("  none\n", style="dim")
        marks = missing_marks(
            is_image=path.suffix.lower() in IMAGE_EXTENSIONS,
            has_exif=facts.has_exif,
            has_offset=facts.has_offset,
            has_gps=facts.has_gps,
            has_sidecar_geo=bool(facts.geodata_raw),
            json_mode=self.rules.use_json_data,
            sidecar_found=facts.sidecar is not None,
            json_error=facts.json_error,
        )
        if marks:
            words = [MISSING_NAME[char] for char in marks if char in MISSING_NAME]
            text.append("Missing  " + ", ".join(words) + "\n")

    def _update_status(self) -> None:
        widget = self.query_one("#status", Static)
        if self._message:
            widget.update(self._message)
            return
        if self._leader:
            widget.update("g then g: first file")
            return
        if not self.files:
            widget.update("")
            return
        path = self.files[self._cursor]
        new_name = self.preview_for(self._cursor)
        query = self._match_query()
        if query:
            matched = highlighted_name(path.name, new_name, query)
            if matched is not None:
                widget.update(matched)
                return
        if new_name == path.name:
            widget.update(Text.assemble(path.name, (" (unchanged)", "dim")))
            return
        text = Text()
        for tag, piece in name_diff_ops(path.name, new_name):
            style = {"equal": "", "delete": "red strike", "insert": "bold green"}[tag]
            text.append(piece, style=style)
        widget.update(text)

    def _update_subtitle(self) -> None:
        if self.visual:
            mode = "VISUAL"
        elif self.date_mode:
            mode = "FILENAME DATE"
        elif self.mtime_mode:
            mode = "SET MTIME"
        else:
            mode = "NORMAL"
        total = len(self.files)
        pos = min(self._cursor + 1, total) if total else 0
        place = "Prefix" if self.rules.use_prefix else "Suffix"
        zone = self.rules.source_tz or "Local"
        extra = []
        if self.selected:
            extra.append(f"Sel:{len(self.selected)}")
        if self.locked:
            extra.append(f"Lock:{len(self.locked)}")
        if self.manual:
            extra.append(f"Man:{len(self.manual)}")
        tail = ("  " + " ".join(extra)) if extra else ""
        changing = len(self._changing)
        count = f"{changing} of {total} will change"
        warnings = sum(
            1
            for index, file_path in enumerate(self.files)
            if is_warning(self.facts_for(file_path), self._neighbor_at(index))
        )
        warning = f"  W:{warnings}" if warnings else ""
        found = ""
        if self._find_open or self._search_active:
            needle = self._draft if self._find_open else self._search_query
            found = "  " + find_label(self._cursor, self._hit_indexes(needle))
        self.sub_title = f"{mode}  {pos}/{total}  {place}  {zone}  {count}{warning}{found}{tail}"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._rebuilding:
            return
        row = event.cursor_row
        if self._search_nav:
            if row != self._cursor:
                table = self.query_one(FileTable)
                if table.row_count:
                    table.move_cursor(row=self._cursor)
                return
            self._search_nav = False
            self._update_detail()
            self._update_timeline()
            self._update_status()
            self._update_subtitle()
            return
        if self.visual and self.anchor is not None and row != self._cursor:
            old = self._cursor
            old_block = set(range(min(self.anchor, old), max(self.anchor, old) + 1))
            new_block = set(range(min(self.anchor, row), max(self.anchor, row) + 1))
            self.selected.update(new_block - old_block)
            self.selected.difference_update(old_block - new_block)
            self._cursor = row
            self._rebuilding = True
            try:
                self._fill_table()
            finally:
                self._rebuilding = False
        else:
            self._cursor = row
        self._update_detail()
        self._update_timeline()
        self._update_status()
        self._update_subtitle()

    def _move_to(self, row: int) -> None:
        if not self.files:
            return
        table = self.query_one(FileTable)
        self._cursor = max(0, min(row, len(self.files) - 1))
        if table.row_count:
            table.move_cursor(row=self._cursor)

    def _targets(self) -> list[int]:
        if self.selected:
            return sorted(index for index in self.selected if index < len(self.files))
        if self.files and self._cursor < len(self.files):
            return [self._cursor]
        return []

    def _ask(self, question: str, on_yes: Callable[[], None]) -> None:
        def done(yes: bool | None) -> None:
            if yes:
                on_yes()
            else:
                self.set_status("Cancelled.")
                self._update_status()

        self.push_screen(ConfirmScreen(question), done)

    def _cancel_modes_on_escape(self) -> bool:
        if self.visual:
            self.visual = False
            self.anchor = None
            self.set_status("Visual select off")
            return True
        if self.date_mode:
            self.date_mode = False
            self.set_status("Filename date mode off")
            return True
        if self.mtime_mode:
            self.mtime_mode = False
            self.set_status("Set mtime mode off")
            return True
        return False

    def action_escape(self) -> None:
        if self._find_open:
            self._close_find(restore=True)
            return
        if self._zoom:
            self._set_zoom(None)
            self.query_one(FileTable).focus()
            return
        if self._cancel_modes_on_escape():
            self.refresh_view()

    def _set_zoom(self, which: str | None) -> None:
        self._zoom = which
        for name in ("files", "detail", "options", "preview"):
            self.screen.set_class(which == name, f"-zoom-{name}")

    def _zoom_to(self, which: str, target_id: str) -> None:
        self._begin()
        self._set_zoom(None if self._zoom == which else which)
        if self._zoom is None:
            self.query_one(FileTable).focus()
        else:
            self.query_one(f"#{target_id}").focus()

    def action_focus_files(self) -> None:
        self._zoom_to("files", "files")

    def action_focus_detail(self) -> None:
        self._zoom_to("detail", "detail")

    def action_focus_rules(self) -> None:
        self._zoom_to("options", "options")

    def action_focus_preview(self) -> None:
        self._zoom_to("preview", "preview-slot")

    def action_help(self) -> None:
        self._begin()

        def done(run: str | None) -> None:
            if not run:
                return
            spec = next(item for item in COMMAND_SPECS if item.run == run)
            _invoke(self, spec)

        self.push_screen(CommandScreen(self._command_context()), done)

    def _command_context(self) -> CommandContext:
        return CommandContext(
            file_count=len(self.files),
            selected=len(self.selected),
            locked=len(self.locked),
            undo_count=len(self.journal.batches),
            date_mode=self.date_mode,
            mtime_mode=self.mtime_mode,
        )

    def action_cursor_down(self) -> None:
        self._begin()
        table = self.query_one(FileTable)
        if table.row_count:
            table.action_cursor_down()

    def action_cursor_up(self) -> None:
        self._begin()
        table = self.query_one(FileTable)
        if table.row_count:
            table.action_cursor_up()

    def action_page_down(self) -> None:
        self._begin()
        table = self.query_one(FileTable)
        if table.row_count:
            table.action_page_down()

    def action_page_up(self) -> None:
        self._begin()
        table = self.query_one(FileTable)
        if table.row_count:
            table.action_page_up()

    def action_leader_g(self) -> None:
        if self._leader:
            self._leader = False
            self._move_to(0)
            self.set_status("")
            self._update_status()
        else:
            self._leader = True
            self.set_status("g then g: first file")
            self._update_status()

    def action_goto_top(self) -> None:
        self._begin()
        self._move_to(0)

    def action_goto_end(self) -> None:
        self._begin()
        if self.files:
            self._move_to(len(self.files) - 1)

    def action_next_change(self) -> None:
        if self._search_active:
            self._jump_hits(
                self._hit_indexes(),
                1,
                "No matches.",
                "No other match.",
                from_search=True,
            )
            return
        hits = [index for index in range(len(self.files)) if index in self._changing]
        self._jump_hits(hits, 1, "No files will change.", "No other file will change.")

    def action_prev_change(self) -> None:
        if self._search_active:
            self._jump_hits(
                self._hit_indexes(),
                -1,
                "No matches.",
                "No other match.",
                from_search=True,
            )
            return
        hits = [index for index in range(len(self.files)) if index in self._changing]
        self._jump_hits(hits, -1, "No files will change.", "No other file will change.")

    def action_next_changed(self) -> None:
        hits = [index for index in range(len(self.files)) if index in self._changing]
        self._jump_hits(hits, 1, "No files will change.", "No other file will change.")

    def action_prev_changed(self) -> None:
        hits = [index for index in range(len(self.files)) if index in self._changing]
        self._jump_hits(hits, -1, "No files will change.", "No other file will change.")

    def action_search_again(self) -> None:
        if not self._search_active:
            self.set_status("No search.")
            self._update_status()
            return
        self.action_next_change()

    def action_find(self) -> None:
        if self._find_open or len(self.screen_stack) > 1:
            return
        self._open_find()

    def action_preview(self) -> None:
        self._begin()
        self._set_preview(not self.preview_on)

    def _set_preview(self, value: bool) -> None:
        self.preview_on = value
        self.query_one("#preview-slot").set_class(not self.preview_on, "-off")
        self._preview_gen += 1
        if self.preview_on:
            self._schedule_preview()
            self.set_status("Preview on")
        else:
            self.set_status("Preview off")
        self._sync_options()
        self._update_status()

    def _open_find(self) -> None:
        self._begin()
        self._find_open = True
        self._find_origin = self._cursor
        box = self.query_one("#find", Input)
        prefill = self._search_query if self._search_active else ""
        box.add_class("-open")
        box.value = prefill
        self._apply_query(prefill)
        box.focus()

    def _close_find(self, *, restore: bool) -> None:
        self._find_open = False
        self._leader = False
        self._message = ""
        self.query_one("#find", Input).remove_class("-open")
        if restore and self.files:
            self._search_nav = True
            self._cursor = max(0, min(self._find_origin, len(self.files) - 1))
        self.refresh_view()
        table = self.query_one(FileTable)
        if table.row_count:
            table.focus()

    def _apply_query(self, query: str) -> None:
        self._draft = query
        if not self.files:
            self._update_subtitle()
            self._update_status()
            return
        hits = self._hit_indexes(query)
        if query and hits:
            target = first_at_or_after(self._find_origin, hits)
        else:
            target = self._find_origin
        self._search_nav = True
        self._cursor = max(0, min(target, len(self.files) - 1))
        self.refresh_view()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "find" or not self._find_open:
            return
        self._apply_query(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "find" or not self._find_open:
            return
        query = event.value
        if query == "":
            self._search_active = False
            self._search_query = ""
            self._close_find(restore=False)
            return
        if not self._hit_indexes(query):
            self._close_find(restore=True)
            return
        self._search_query = query
        self._search_active = True
        self._draft = query
        self._close_find(restore=False)

    def _schedule_preview(self) -> None:
        if not self.is_running:
            return
        timer = self._preview_timer
        if timer is not None:
            timer.stop()
        self._preview_timer = self.set_timer(file_preview.DEBOUNCE_SECONDS, self._kick_preview)

    def _kick_preview(self) -> None:
        self._preview_timer = None
        if not self.is_running or not self.preview_on or not self.files:
            return
        try:
            detail = self.query_one("#detail")
        except NoMatches:
            return
        if file_preview.pane_too_small(detail.size.width, detail.size.height):
            self._preview_gen += 1
            self._show_preview_caption("pane too small", "")
            return
        self._preview_gen += 1
        self._load_preview(str(self.files[self._cursor]), self._preview_gen)

    @work(thread=True, exclusive=True, group="preview", exit_on_error=False)
    def _load_preview(self, path_str: str, generation: int) -> None:
        path = Path(path_str)
        try:
            result = file_preview.build_preview(path, self._thumbs)
        except Exception:
            result = file_preview.plain_preview(path)
        self.call_from_thread(self._apply_preview, path_str, generation, result)

    def _apply_preview(self, path_str: str, generation: int, result: file_preview.Preview) -> None:
        if not self._preview_current(generation, path_str):
            return
        self.run_worker(
            self._show_preview(result, generation, path_str),
            group="preview-show",
            exclusive=True,
            exit_on_error=False,
        )

    def _preview_current(self, generation: int, path_str: str) -> bool:
        if generation != self._preview_gen or not self.preview_on:
            return False
        return bool(self.files) and str(self.files[self._cursor]) == path_str

    def _show_preview_caption(self, caption: str, body: str) -> None:
        self.run_worker(
            self._show_preview_caption_now(caption, body),
            group="preview-show",
            exclusive=True,
            exit_on_error=False,
        )

    async def _show_preview_caption_now(self, caption: str, body: str) -> None:
        await self.query("#preview-image").remove()
        self.query_one("#preview-caption", Static).update(caption)
        fallback = self.query_one("#preview-fallback", Static)
        fallback.update(body)
        fallback.display = bool(body)

    async def _show_preview(
        self,
        result: file_preview.Preview,
        generation: int,
        path_str: str,
    ) -> None:
        if not self._preview_current(generation, path_str):
            return
        # remove() only schedules a prune. The id stays until that finishes.
        # Mounting preview-image again in the same call raises DuplicateIds,
        # which the handler was showing as "Picture unavailable" on every
        # second file.
        await self.query("#preview-image").remove()
        if not self._preview_current(generation, path_str):
            return
        self.query_one("#preview-caption", Static).update(result.caption)
        fallback = self.query_one("#preview-fallback", Static)
        if result.show_image and result.image is not None:
            widget_cls = file_preview.load_image_widget()
            if widget_cls is None:
                fallback.update(result.caption)
                fallback.display = True
                return
            try:
                await self.query_one("#preview-slot").mount(
                    widget_cls(
                        result.image,
                        id="preview-image",
                        on_error=self._preview_error,
                    )
                )
            except Exception:
                await self.query("#preview-image").remove()
                if self._preview_current(generation, path_str):
                    fallback.update("Picture unavailable")
                    fallback.display = True
                return
            if not self._preview_current(generation, path_str):
                await self.query("#preview-image").remove()
                return
            fallback.update("")
            fallback.display = False
            return
        fallback.update(result.body)
        fallback.display = bool(result.body)

    def _preview_error(self, error: Exception) -> Static:
        del error
        return Static("Picture unavailable")

    def action_next_warning(self) -> None:
        hits = [
            index
            for index, path in enumerate(self.files)
            if is_warning(self.facts_for(path), self._neighbor_at(index))
        ]
        self._jump_hits(hits, 1, "No warnings.", "No other warning.")

    def _jump_hits(
        self,
        hits: list[int],
        step: int,
        none_msg: str,
        only_msg: str,
        from_search: bool = False,
    ) -> None:
        self._begin()
        if not hits:
            self.set_status(none_msg)
            self._update_status()
            return
        if step > 0:
            later = [index for index in hits if index > self._cursor]
            target = later[0] if later else hits[0]
        else:
            earlier = [index for index in hits if index < self._cursor]
            target = earlier[-1] if earlier else hits[-1]
        if target == self._cursor:
            self.set_status(only_msg)
            self._update_status()
            return
        if from_search:
            self._search_nav = True
        self._move_to(target)

    def action_toggle_select(self) -> None:
        self._begin()
        if self.visual:
            self.visual = False
            self.anchor = None
            self.set_status("Visual select off")
            self.refresh_view()
            return
        if not self.files:
            return
        if self._cursor in self.selected:
            self.selected.remove(self._cursor)
        else:
            self.selected.add(self._cursor)
        self.refresh_view()

    def action_visual(self) -> None:
        self._begin()
        self.visual = not self.visual
        if self.visual and self.files:
            self.anchor = self._cursor
            self.selected.add(self._cursor)
            self.set_status("Visual select on")
        else:
            self.anchor = None
            self.visual = False
            self.set_status("Visual select off")
        self.refresh_view()

    def action_select_all(self) -> None:
        self._begin()
        self.selected = set(range(len(self.files)))
        self.set_status(f"Selected all {len(self.files)} files")
        self.refresh_view()

    def action_select_none(self) -> None:
        self._begin()
        self.selected.clear()
        self.visual = False
        self.anchor = None
        self.set_status("Deselected all files")
        self.refresh_view()

    def action_toggle(self, field: str) -> None:
        self._begin()
        self._set_rule(field, not getattr(self.rules, field))

    def _set_rule(self, field: str, value: bool) -> None:
        setattr(self.rules, field, value)
        if field == "use_json_data":
            self._facts.clear()
            state = "on" if self.rules.use_json_data else "off"
            self.set_status(f"JSON data is {state}")
        if field == "add_datetime" and self.rules.add_datetime:
            self.rules.datetime_stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.refresh_view()

    def _sync_options(self) -> None:
        self._syncing = True
        try:
            for _key, field, _label in OPTION_FIELDS:
                self.query_one(f"#opt-{field}", OptionToggle).set_value(
                    bool(getattr(self.rules, field))
                )
            self.query_one("#opt-date_mode", OptionToggle).set_value(self.date_mode)
            self.query_one("#opt-mtime_mode", OptionToggle).set_value(self.mtime_mode)
            self.query_one("#opt-preview", OptionToggle).set_value(self.preview_on)
            zone = self.rules.source_tz or "Local"
            self.query_one("#act-zone", Button).label = f"T {zone}"
        except NoMatches:
            return
        finally:
            self._syncing = False

    @on(OptionToggle.Changed)
    def _option_toggled(self, event: OptionToggle.Changed) -> None:
        if self._syncing:
            return
        box_id = event.toggle.id or ""
        if box_id == "opt-date_mode":
            if event.value == self.date_mode:
                return
            self._begin()
            self._assign_date_mode(event.value)
            return
        if box_id == "opt-mtime_mode":
            if event.value == self.mtime_mode:
                return
            self._begin()
            self._assign_mtime_mode(event.value)
            return
        if box_id == "opt-preview":
            self._begin()
            if event.value != self.preview_on:
                self._set_preview(event.value)
            return
        field = box_id.removeprefix("opt-")
        if field not in TOGGLE_FIELDS.values():
            return
        if getattr(self.rules, field) == event.value:
            return
        self._begin()
        self._set_rule(field, event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        run = {
            "act-apply": "apply",
            "act-undo": "undo",
            "act-zone": "timezone",
            "act-write-zone": "write_timezone",
            "act-gps": "write_geodata",
            "act-refresh": "reload_files",
            "act-reset": "reset_rules",
        }.get(event.button.id or "")
        if run:
            getattr(self, f"action_{run}")()

    def action_reset_rules(self) -> None:
        self._begin()
        reset_name_rules(self.rules)
        self._facts.clear()
        self.set_status("Name options reset.")
        self.refresh_view()

    def action_lock(self) -> None:
        self._begin()
        if self.date_mode or self.mtime_mode:
            self.set_status("Date or mtime mode is on.")
            self._update_status()
            return
        if not self.files:
            return
        index = self._cursor
        if index in self.locked:
            del self.locked[index]
            self.set_status("Unlocked this file")
        else:
            path = self.files[index]
            new_name = self.manual.get(index) or preview_name(
                path.name, self.facts_for(path), self.rules
            )
            if new_name != path.name:
                self.locked[index] = new_name
                self.set_status("Locked this file")
            else:
                self.set_status("No changes to lock")
        self.refresh_view()

    def action_lock_selected(self) -> None:
        self._begin()
        if self.date_mode or self.mtime_mode:
            self.set_status("Date or mtime mode is on.")
            self._update_status()
            return
        if not self.selected:
            self.set_status("No files selected to lock")
            self._update_status()
            return
        locked = 0
        for index in self.selected:
            if index >= len(self.files) or index in self.locked:
                continue
            path = self.files[index]
            new_name = self.manual.get(index) or preview_name(
                path.name, self.facts_for(path), self.rules
            )
            if new_name != path.name:
                self.locked[index] = new_name
                locked += 1
        if locked:
            self.set_status(f"Locked {locked} files.")
        else:
            self.set_status("Selected files have no changes to lock.")
        self.refresh_view()

    def action_unlock_all(self) -> None:
        self._begin()
        if not self.locked:
            self.set_status("No files are locked")
        else:
            count = len(self.locked)
            self.locked.clear()
            self.set_status(f"Unlocked all {count} locked files.")
        self.refresh_view()

    def action_date_mode(self) -> None:
        self._begin()
        self._assign_date_mode(not self.date_mode)

    def _assign_date_mode(self, value: bool) -> None:
        self.date_mode = value
        if self.date_mode:
            self.mtime_mode = False
        state = "on" if self.date_mode else "off"
        self.set_status(f"Filename date mode {state}")
        self.refresh_view()

    def action_mtime_mode(self) -> None:
        self._begin()
        self._assign_mtime_mode(not self.mtime_mode)

    def _assign_mtime_mode(self, value: bool) -> None:
        self.mtime_mode = value
        if self.mtime_mode:
            self.date_mode = False
        state = "on" if self.mtime_mode else "off"
        self.set_status(f"Set mtime mode {state}")
        self.refresh_view()

    def action_reload_files(self) -> None:
        self._begin()
        self.reload_files()
        self.set_status("File list refreshed")
        self.refresh_view()

    def action_insert(self) -> None:
        self._begin()
        if self.visual or self.date_mode or self.mtime_mode:
            self.set_status("Cannot enter insert mode while in another mode.")
            self._update_status()
            return
        if not self.files:
            return
        stem, extension = split_stem(self.preview_for(self._cursor))

        def done(result: tuple[str, str] | None) -> None:
            if not result:
                return
            kind, name = result
            self._save_manual(name)
            if kind == "rename":
                self.action_apply()
            else:
                self.refresh_view()

        self.push_screen(InsertScreen(stem, extension), done)

    def _save_manual(self, name: str) -> None:
        cleaned = name.strip()
        if cleaned:
            self.manual[self._cursor] = cleaned
            self.set_status("Manual edit applied.")
        elif self._cursor in self.manual:
            del self.manual[self._cursor]
            self.set_status("Manual edit cleared.")

    def action_timezone(self) -> None:
        self._begin()
        if not ZONEINFO_AVAILABLE:
            self.set_status("Timezone selection needs zoneinfo.")
            self._update_status()
            return

        def done(selected: str | None) -> None:
            if not selected:
                self.set_status("Timezone selection cancelled.")
                self._update_status()
                return
            if selected == "Local System":
                self.rules.source_tz = None
                self.set_status("Timezone override cleared (using system time).")
            else:
                try:
                    zone = resolve_tz(selected)
                except Exception:
                    self.set_status(f"Timezone '{selected}' was not found.")
                    self._update_status()
                    return
                self.rules.source_tz = selected
                offset = format_offset(datetime.now(zone))
                self.set_status(f"Timezone set to {selected} ({offset}).")
            self._facts.clear()
            self.refresh_view()

        self.push_screen(TimezoneScreen(self.rules.source_tz), done)

    def _rename_pairs(self) -> list[tuple[Path, str]]:
        names: dict[int, str] = dict(self.locked)
        for index, name in self.manual.items():
            names.setdefault(index, name)
        for index in self.selected:
            if index in names or index >= len(self.files):
                continue
            path = self.files[index]
            new_name = preview_name(path.name, self.facts_for(path), self.rules)
            if new_name != path.name:
                names[index] = new_name
        pairs: list[tuple[Path, str]] = []
        for index, name in sorted(names.items()):
            if index < len(self.files):
                pairs.append((self.files[index], name))
        return pairs

    def action_apply(self) -> None:
        if self._find_open:
            return
        self._begin()
        if self.visual:
            self.visual = False
            self.anchor = None
            self.set_status("Visual select off")
            self.refresh_view()
            return
        if self.date_mode:
            self._ask_dates()
            return
        if self.mtime_mode:
            self._ask_mtime()
            return
        pairs = [(src, name) for src, name in self._rename_pairs() if src.name != name]
        if not pairs:
            self.set_status("No changes to apply.")
            self._update_status()
            return
        self._ask(f"Rename {len(pairs)} files?", lambda: self._do_rename(pairs))

    def _do_rename(self, pairs: list[tuple[Path, str]]) -> None:
        outcome = rename_paths(pairs)
        self.journal.push(Batch("rename", [RenameOp(src, dst) for src, dst in outcome.done]))
        self.reload_files()
        message = f"Renamed {len(outcome.done)} files."
        if outcome.conflicts:
            message += f" {outcome.conflicts} conflicts."
        if outcome.errors:
            message += f" {len(outcome.errors)} errors."
            self.debug("; ".join(outcome.errors))
        self.set_status(message)
        self.refresh_view()

    def _ask_mtime(self) -> None:
        if not self.selected:
            self.set_status("No files selected. Press space to select.")
            self._update_status()
            return
        indexes = [index for index in sorted(self.selected) if index < len(self.files)]
        self._ask(
            f"Set mtime for {len(indexes)} files?",
            lambda: self._do_mtime(indexes),
        )

    def _do_mtime(self, indexes: list[int]) -> None:
        local = _local_zone()
        batch = Batch("mtime")
        updated = 0
        errors = 0
        for index in indexes:
            path = self.files[index]
            _label, moment = mtime_source(self.facts_for(path), self.rules, local)
            if moment is None:
                errors += 1
                continue
            op = self.journal.snapshot(path, mode="times")
            try:
                stamp = moment.timestamp()
                os.utime(path, (stamp, stamp))
            except OSError as exc:
                errors += 1
                self.debug(f"Error setting mtime: {exc}")
                continue
            batch.ops.append(op)
            updated += 1
        self.journal.push(batch)
        self._facts.clear()
        self.mtime_mode = False
        message = f"Updated mtime for {updated} files."
        if errors:
            message += f" {errors} failed."
        self.set_status(message)
        self.refresh_view()

    def _ask_dates(self) -> None:
        if self.selected:
            paths = [
                self.files[index] for index in sorted(self.selected) if index < len(self.files)
            ]
        else:
            paths = list(self.files)
        plan = plan_filename_dates(paths, self.plan_zone())
        if not plan.todo:
            if plan.same:
                self.set_status(
                    f"Nothing to do: {len(plan.same)} file(s) already match their filename."
                )
            else:
                self.set_status(
                    "No date found in the filename(s). Expected e.g. 2024-01-31-142500 or 20240131."
                )
            self._update_status()
            return
        zone = self.target_zone()
        offset = format_offset(datetime.now(zone))
        name = self.rules.source_tz or "Local System"
        n_time = sum(1 for item in plan.todo if item.prec in ("second", "minute"))
        n_noon = sum(1 for item in plan.todo if item.prec == "date")
        n_nudge = sum(1 for item in plan.todo if item.nudged)
        parts = [
            part
            for part in (
                f"{n_time} with time" if n_time else "",
                f"{n_noon} date-only set to 12:00" if n_noon else "",
                f"{n_nudge} nudged by 1 second" if n_nudge else "",
            )
            if part
        ]
        question = (
            f"Set dates and EXIF offsets ({offset}) for {len(plan.todo)} file(s)?\n"
            f"Timezone: {name}. Sets mtime, birth time, and EXIF offset tags.\n"
        )
        if parts:
            question += ", ".join(parts) + "\n"
        question += "y or n"
        todo = list(plan.todo)
        self._ask(question, lambda: self._do_dates(todo))

    def _do_dates(self, todo: list[DatePlanItem]) -> None:
        zone = self.target_zone()
        batch = Batch("dates")
        ok = 0
        errors = 0
        meta_ok = 0
        notes: list[str] = []
        for item in todo:
            try:
                moment = datetime.fromtimestamp(item.target, tz=zone)
            except (OSError, OverflowError, ValueError) as exc:
                errors += 1
                self.debug(f"Error setting date for {item.path.name}: {exc}")
                continue
            try:
                op = self.journal.snapshot(item.path)
            except OSError as exc:
                errors += 1
                self.debug(f"Snapshot failed for {item.path.name}: {exc}")
                continue
            if not set_filesystem_times(item.path, moment, setfile=self.setfile):
                errors += 1
                continue
            if write_file_metadata_and_offset(item.path, moment, exiftool=self.exiftool):
                meta_ok += 1
            ok += 1
            if op is not None:
                batch.ops.append(op)
                if op.copy_failed:
                    notes.append(f"{item.path.name}: metadata cannot be undone")
        self.journal.push(batch)
        self._facts.clear()
        self.date_mode = False
        note = f" ({meta_ok} EXIF or video tags written)" if meta_ok else ""
        message = f"Set dates on {ok} file(s){note}."
        if errors:
            message += f" {errors} failed."
        if notes:
            message += " " + " ".join(notes)
        self.set_status(message)
        self.refresh_view()

    def action_write_timezone(self) -> None:
        self._begin()
        indexes = self._targets()
        if not indexes:
            self.set_status("No files selected.")
            self._update_status()
            return
        zone = self.target_zone()
        offset = format_offset(datetime.now(zone))
        name = self.rules.source_tz or "Local System"
        question = (
            f"Write EXIF date and timezone offset ({offset}) to {len(indexes)} file(s)?\n"
            f"Timezone: {name}\n"
            "Sets DateTimeOriginal, OffsetTimeOriginal, and filesystem dates.\n"
            "y or n"
        )
        self._ask(question, lambda: self._do_timezone(indexes))

    def _do_timezone(self, indexes: list[int]) -> None:
        zone = self.target_zone()
        batch = Batch("timezone")
        counts = WriteCounts()
        notes: list[str] = []
        for index in indexes:
            path = self.files[index]
            moment = datetime_for_write(path, self.facts_for(path), zone)
            if moment is None:
                counts.skipped += 1
                continue
            try:
                op = self.journal.snapshot(path)
            except OSError as exc:
                counts.skipped += 1
                self.debug(f"Snapshot failed for {path.name}: {exc}")
                continue
            if op.copy_failed:
                notes.append(f"{path.name}: metadata cannot be undone")
            filesystem_ok = set_filesystem_times(path, moment, setfile=self.setfile)
            if can_write_metadata(path):
                metadata_ok: bool | None = write_file_metadata_and_offset(
                    path, moment, exiftool=self.exiftool
                )
            else:
                metadata_ok = None
            tally_write(counts, filesystem_ok=filesystem_ok, metadata_ok=metadata_ok)
            if filesystem_ok or metadata_ok:
                batch.ops.append(op)
            if metadata_ok is False:
                self.debug(f"Metadata write failed for {path.name}")
        self.journal.push(batch)
        self._facts.clear()
        message = timezone_write_message(counts)
        if notes:
            message += " " + " ".join(notes)
        self.set_status(message)
        self.refresh_view()

    def action_write_geodata(self) -> None:
        self._begin()
        if not PIEXIF_AVAILABLE:
            self.set_status("Cannot write EXIF. piexif is not installed.")
            self._update_status()
            return
        indexes = [
            index
            for index in self._targets()
            if self.files[index].suffix.lower() in IMAGE_EXTENSIONS
        ]
        if not indexes:
            self.set_status("No compatible image files selected (.jpg, .tif).")
            self._update_status()
            return
        self._ask(
            f"Write geodata to {len(indexes)} files?",
            lambda: self._do_geodata(indexes),
        )

    def _do_geodata(self, indexes: list[int]) -> None:
        batch = Batch("geodata")
        updated = 0
        errors = 0
        for index in indexes:
            path = self.files[index]
            raw = self.facts_for(path).geodata_raw
            if not raw:
                errors += 1
                continue
            try:
                op = self.journal.snapshot(path)
            except OSError as exc:
                errors += 1
                self.debug(f"Snapshot failed for {path.name}: {exc}")
                continue
            if write_geodata(path, raw["latitude"], raw["longitude"]):
                updated += 1
                if op is not None:
                    batch.ops.append(op)
            else:
                errors += 1
                self.debug(f"Failed to write geodata to {path.name}")
        self.journal.push(batch)
        self._facts.clear()
        message = f"Wrote geodata to {updated} files."
        if errors:
            message += f" {errors} failed."
        self.set_status(message)
        self.refresh_view()

    def action_undo(self) -> None:
        self._begin()

        def restore_birth(path: Path, birth: float) -> None:
            set_birthtime(path, datetime.fromtimestamp(birth).astimezone(), setfile=self.setfile)

        result = self.journal.undo(restore_birth=restore_birth)
        if result is None:
            self.set_status("Nothing to undo.")
            self._update_status()
            return
        self.reload_files()
        message = f"Undid {result.kind} ({result.restored} restored)."
        if result.notes:
            message += " " + " ".join(result.notes)
        self.set_status(message)
        self.refresh_view()


def _local_zone() -> tzinfo:
    zone = datetime.now().astimezone().tzinfo
    assert zone is not None
    return zone


def _format_stamp(stamp: float, zone: tzinfo | None) -> str:
    return datetime.fromtimestamp(stamp, tz=zone).strftime("%Y-%m-%d %H:%M:%S")
