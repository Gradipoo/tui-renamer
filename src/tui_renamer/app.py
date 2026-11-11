"""Textual UI for rn."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, tzinfo
from pathlib import Path

from rich.style import Style
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
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    Select,
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
    DatePlan,
    DatePlanItem,
    FileFacts,
    RenameRules,
    assign_unique_names,
    collision_stem,
    datetime_for_write,
    delta_text,
    earliest_date,
    earliest_source,
    filename_clock_text,
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
    preview_base,
    preview_name,
    rename_paths,
    reset_name_rules,
    resolve_tz,
    show_not_oldest_warning,
    timeline_bins,
    utc_from_timestamp,
)
from tui_renamer.journal import Batch, Journal, MetaOp, RenameOp
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
    write_image_description,
)
from tui_renamer.scan_backend import OllamaBackend, ScanBackend
from tui_renamer.scan_bits import (
    PendingScan,
    ScanSession,
    StageResult,
    apply_scan_suffix,
    format_scan_parts,
    format_scan_selected,
    strip_accepted_suffix,
)
from tui_renamer.scan_config import load_scan_settings
from tui_renamer.scan_notes import (
    ScanNotes,
    notes_path_for,
    notes_renames,
    rewrite_pairing_names,
    save_notes,
)
from tui_renamer.scan_ui import ReviewScreen
from tui_renamer.search import (
    find_label,
    highlighted_name,
    kept_span,
    match_span,
    query_hits,
)
from tui_renamer.table_view import FILTERS, SORTS, TableView, embedded_capture, matches_view
from tui_renamer.view_config import ViewState, clear_view, load_view, save_view

TOGGLE_FIELDS = {
    "E": "use_earliest",
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
    "use_earliest": "Stage the earliest available date for each filename",
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
    "act-scan": "Review this photo",
}


OPTION_FIELDS = (
    ("d", "add_datetime", "Date"),
    ("m", "add_modtime", "Modified time"),
    ("o", "add_origtime", "Original date"),
    ("e", "use_json_data", "JSON"),
    ("b", "add_geodata", "Geo"),
    ("t", "use_prefix", "Prefix"),
    ("x", "strip_original_name", "Strip"),
    ("y", "strip_to_text", "Text"),
    ("c", "remove_caps", "lower"),
    ("s", "remove_special", "Remove symbols"),
    ("p", "replace_spaces", "Spaces → dashes"),
    (".", "replace_dots", "Dots → dashes"),
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
            with VerticalScroll(id="confirm-details"):
                yield Static(self.question, id="confirm-question", markup=False)
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


class ResultScreen(ModalScreen[None]):
    """Read-only details for the most recent batch operation."""

    BINDINGS = [Binding("escape,q", "dismiss(None)", show=False)]

    def __init__(self, report: str) -> None:
        super().__init__()
        self.report = report

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Last batch result", markup=False)
            with VerticalScroll(id="result-scroll"):
                yield Static(self.report, id="result-details", markup=False)
            yield Button("Back", id="result-close")

    def on_mount(self) -> None:
        self.query_one("#result-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "result-close":
            self.dismiss(None)


class FilterSortScreen(ModalScreen[TableView | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]
    DEFAULT_CSS = """
    FilterSortScreen { align: center middle; }
    FilterSortScreen #view-dialog {
        width: 64; max-width: 98%; height: auto; max-height: 95%;
        border: solid $primary; background: $surface; padding: 1 2;
    }
    FilterSortScreen Checkbox { height: auto; }
    FilterSortScreen #view-actions { height: auto; }
    """

    def __init__(self, view: TableView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="view-dialog"):
            yield Static("Filter and sort", markup=False)
            yield Static("All checked filters must match. Staged work is preserved.")
            for key, label in FILTERS:
                yield Checkbox(label, key in self.view.filters, id=f"filter-{key}", compact=True)
            yield Input(
                self.view.extension,
                placeholder="Extension, e.g. jpg (blank = all)",
                id="view-extension",
            )
            yield Select(SORTS, value=self.view.sort, allow_blank=False, id="view-sort")
            yield Checkbox(
                "Descending",
                self.view.descending,
                id="view-descending",
                compact=True,
            )
            with Horizontal(id="view-actions"):
                yield Button("Show", id="view-show", variant="primary")
                yield Button("Reset", id="view-reset")
                yield Button("Cancel", id="view-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "view-reset":
            self.dismiss(TableView())
        elif event.button.id == "view-cancel":
            self.dismiss(None)
        elif event.button.id == "view-show":
            self.dismiss(
                TableView(
                    filters=frozenset(
                        key
                        for key, _label in FILTERS
                        if self.query_one(f"#filter-{key}", Checkbox).value
                    ),
                    extension=self.query_one("#view-extension", Input).value.strip(),
                    sort=str(self.query_one("#view-sort", Select).value),
                    descending=self.query_one("#view-descending", Checkbox).value,
                )
            )


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
    """Stage a name without writing to disk."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, stem: str, extension: str) -> None:
        super().__init__()
        self.stem = stem
        self.extension = extension

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Edit name", markup=False)
            yield Input(value=self.stem, id="stem", select_on_focus=True)
            yield Static(self.extension or "(no extension)", id="extension", markup=False)
            yield Static(
                "Enter stages this name. Apply on the main screen writes changes.", markup=False
            )
            with Horizontal(classes="edit-actions"):
                yield Button("Stage name", id="edit-stage", variant="primary")
                yield Button("Cancel", id="edit-cancel")

    def on_mount(self) -> None:
        self.query_one("#stem", Input).focus()

    def _full(self) -> str:
        return self.query_one("#stem", Input).value + self.extension

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(("save", event.value + self.extension))

    @on(Button.Pressed, "#edit-stage")
    def stage_name(self) -> None:
        self.dismiss(("save", self._full()))

    @on(Button.Pressed, "#edit-cancel")
    def cancel_name(self) -> None:
        self.dismiss(None)


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
        Binding("S", "app.scan_review", show=False),
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
    BINDINGS = [Binding("enter,space", "toggle", show=False)]

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
        # Gap space uses its own style so it stays a separate segment. A plain
        # space merges into the label run, and SVG textLength then squeezes it.
        text = Text()
        text.append(" ")
        text.append(self.key_name, style="bold underline")
        text.append(" ", style=Style(bold=False))
        text.append(f"{self.caption} {'ON' if self._value else 'off'} ")
        return text

    def set_value(self, value: bool) -> None:
        """Show `value` without posting Changed, so a refresh cannot toggle twice."""
        self._value = value
        self.set_class(value, "-on")
        self.update(self._chip())

    def get_content_width(self, container: Size, viewport: Size) -> int:
        del container, viewport
        return 1 + len(self.key_name) + 1 + len(self.caption) + 5

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        del container, viewport, width
        return 1

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_toggle()

    def action_toggle(self) -> None:
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
        yield Static("Filename preview · no disk writes", classes="option-heading")
        with WrappingRow(id="opt-toggles"):
            yield Button(
                "E Use earliest date",
                id="opt-use_earliest",
                compact=True,
                tooltip=TOGGLE_TIPS["use_earliest"],
            )
            for key, field, label in OPTION_FIELDS[:6]:
                yield OptionToggle(key, label, tooltip=TOGGLE_TIPS[field], id=f"opt-{field}")
            yield Button("T Timezone", id="act-zone", compact=True, tooltip=BUTTON_TIPS["act-zone"])
            yield Button(
                "0 Reset naming rules",
                id="act-reset",
                compact=True,
                tooltip="Reset filename rules. Keeps manual names, locks and photo-review staging.",
            )
        with Collapsible(title="Filename cleanup", id="group-cleanup"):
            with WrappingRow(classes="option-row", id="opt-cleanup"):
                for key, field, label in OPTION_FIELDS[6:]:
                    yield OptionToggle(key, label, tooltip=TOGGLE_TIPS[field], id=f"opt-{field}")
        with Collapsible(title="Staged edits and selection", id="group-staging"):
            yield Static(
                "Stage changes here; main-screen Apply writes them.", classes="option-note"
            )
            with WrappingRow(classes="option-row"):
                for ident, label, tip in (
                    ("act-edit", "i Edit name", "Stage a manual name for this file"),
                    ("act-scan", "S Photo review", BUTTON_TIPS["act-scan"]),
                    ("act-lock", "l Lock name", "Freeze or unfreeze this proposed name"),
                    ("act-lock-selected", "L Lock selected", "Freeze selected proposed names"),
                    ("act-unlock", "U Unlock all", "Release all frozen names"),
                    ("act-select-all", "a Select visible", "Keep hidden selections too"),
                    (
                        "act-clear-selection",
                        "A Clear selection",
                        "Clear visible and hidden selections; keep staged edits",
                    ),
                    (
                        "act-discard-name",
                        "Discard manual name",
                        "Ask before discarding only this file's manual name",
                    ),
                ):
                    yield Button(label, id=ident, compact=True, tooltip=tip)
        with Collapsible(title="Metadata and file times", id="group-metadata"):
            yield Static("Choose Apply mode · filenames stay unchanged", classes="option-note")
            with WrappingRow(id="opt-modes"):
                yield OptionToggle(
                    "D",
                    "Dates from filenames",
                    tooltip=TOGGLE_TIPS["date_mode"],
                    id="opt-date_mode",
                )
                yield OptionToggle(
                    "M", "Set file times", tooltip=TOGGLE_TIPS["mtime_mode"], id="opt-mtime_mode"
                )
            yield Static("Write actions · each asks for confirmation", classes="option-note")
            with WrappingRow(classes="option-row"):
                for ident, label in (
                    ("act-write-zone", "W Write timezone"),
                    ("act-gps", "B Write GPS"),
                ):
                    yield Button(label, id=ident, compact=True, tooltip=BUTTON_TIPS[ident])
        with Collapsible(title="View and session", id="group-view"):
            with WrappingRow(id="opt-view"):
                yield OptionToggle(
                    "P", "Preview", value=True, tooltip=TOGGLE_TIPS["preview"], id="opt-preview"
                )
                yield Button(
                    "Ctrl+0 Reset preview / zoom",
                    id="act-reset-view",
                    compact=True,
                    tooltip="Reset preview and zoom. Keep filters, naming rules and staged work.",
                )
                yield Button(
                    "r Refresh folder",
                    id="act-refresh",
                    compact=True,
                    tooltip=BUTTON_TIPS["act-refresh"],
                )


FLAG_STYLE = {
    "H": "bold yellow",
    "Y": "red",
    "G": "yellow",
    "!": "bold red",
}
STATUS_LABEL = {
    "UNCHANGED": "Unchanged",
    "MODIFIED": "Preview",
    "SELECTED": "Selected",
    "MANUAL": "Manual name",
    "LOCKED": "Locked name",
    "JSON ERR": "Sidecar err",
    "NO DATE": "No date",
    "UPDATE": "Update",
    "ERR": "Error",
}
BATCH_LABEL = {
    "rename": "rename",
    "apply": "names and metadata",
    "mtime": "file times",
    "dates": "filename dates",
    "timezone": "timezone write",
    "geodata": "GPS write",
}

SOURCE_NAME = {
    "E": "EXIF",
    "J": "JSON sidecar",
    "B": "birth or ctime",
    "M": "mtime",
    "F": "filename",
    "Q": "QuickTime",
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
    .-compact #options { max-height: 25%; }
    .-compact #detail { display: none; }
    .-zoom-detail #detail, .-zoom-preview #detail { display: block; }
    #files { width: 1fr; height: 100%; }
    #detail { width: 1fr; height: 100%; }
    #view-open {
        height: 1; min-height: 1; width: 100%; border: none; margin: 0 1;
        content-align: left middle;
    }
    #earliest-hint { height: auto; padding: 0 1; }
    #options { height: auto; max-height: 40%; overflow-y: auto; padding: 0 1; }
    #options Collapsible { padding: 0; border: none; }
    #options Collapsible > Contents { padding: 0 0 0 1; }
    .option-note { height: auto; color: $text-muted; }
    #batch-bar { height: auto; padding: 0 1; }
    #pending-summary { height: auto; max-height: 3; }
    .option-heading { height: 1; color: $text; text-style: bold; }
    .edit-actions { height: auto; }
    #opt-toggles, #opt-modes, #opt-actions, #opt-view, .option-row {
        layout: grid;
        height: auto;
        width: 100%;
        grid-gutter: 0 1;
    }
    #options OptionToggle, #options Button, #batch-bar Button {
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
    #options OptionToggle:hover, #options Button:hover, #batch-bar Button:hover {
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
    #options OptionToggle:focus, #options Button:focus, #batch-bar Button:focus {
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
    .-zoom-options #options { height: 1fr; max-height: 100%; }
    .-zoom-preview #files { display: none; }
    .-zoom-preview #options { display: none; }
    .-zoom-preview #detail-scroll { display: none; }
    .-zoom-preview #preview-slot { height: 1fr; max-height: 100%; }
    .-zoom-preview #preview-image { height: 1fr; }
    ConfirmScreen, CommandScreen, TimezoneScreen, InsertScreen,
        ReviewScreen, ResultScreen { align: center middle; }
    ReviewScreen #scan-dialog {
        width: 96;
        max-width: 98%;
        height: 90%;
        max-height: 90%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    ReviewScreen #scan-body { height: 1fr; }
    ReviewScreen #scan-left, ReviewScreen #scan-right { width: 1fr; height: 1fr; }
    ReviewScreen.-narrow #scan-body { layout: vertical; }
    ReviewScreen #scan-previews { height: 10; }
    ReviewScreen #scan-orig-slot, ReviewScreen #scan-rel-slot { width: 1fr; height: 10; }
    ReviewScreen.-narrow #scan-previews { layout: vertical; height: auto; }
    ReviewScreen.-narrow #scan-orig-slot, ReviewScreen.-narrow #scan-rel-slot { height: 8; }
    ReviewScreen #scan-name-preview { height: auto; color: $text-muted; }
    ReviewScreen #scan-chips { layout: vertical; height: auto; }
    ReviewScreen .scan-chip-row { height: auto; }
    ReviewScreen .scan-chip { width: 1fr; color: $text-muted; }
    ReviewScreen .scan-meta { width: auto; color: $text-muted; }
    ReviewScreen .scan-chip.-on, ReviewScreen .scan-meta.-on {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    ReviewScreen .scan-chip:focus, ReviewScreen .scan-meta:focus {
        text-style: underline;
    }
    ReviewScreen .scan-chip.-on:focus, ReviewScreen .scan-meta.-on:focus {
        text-style: bold underline;
    }
    ReviewScreen OptionList:focus { border: tall $accent; }
    ReviewScreen #scan-actions { layout: grid; grid-size: 2; height: auto; dock: bottom; }
    ReviewScreen #scan-actions Button {
        width: 1fr; min-width: 0; height: 1; min-height: 1; border: none;
    }
    ReviewScreen .scan-heading { height: 1; text-style: bold; }
    ReviewScreen #scan-name-preview, ReviewScreen #scan-save-hint { height: auto; }
    ReviewScreen #scan-left { width: 2fr; }
    ReviewScreen #scan-model-pick { height: auto; max-height: 8; }
    ReviewScreen #scan-related-list { height: 1fr; min-height: 6; }
    ReviewScreen #scan-help { height: auto; max-height: 10; }
    ReviewScreen #scan-cmd { height: auto; }
    #dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    #confirm-details { height: auto; max-height: 12; }
    #confirm-question { height: auto; }
    CommandScreen #dialog { width: 84; height: 24; }
    #cmd-list { height: 1fr; }
    #zones { height: 12; }
    ResultScreen #dialog { height: 80%; }
    #result-scroll { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "help", "Commands"),
        Binding("h", "help", show=False),
        Binding("f1", "help", show=False),
        Binding("ctrl+p", "help", show=False),
        Binding(":", "help", "Commands"),
        Binding("f", "filter_sort", "Filter/sort"),
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
        Binding("@", "prev_warning", show=False),
        Binding("ctrl+0", "reset_view", show=False),
        Binding("P", "preview", show=False),
        Binding("v", "visual", show=False),
        Binding("a", "select_all", show=False),
        Binding("A", "select_none", show=False),
        Binding("E", "toggle('use_earliest')", show=False),
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
        Binding("S", "scan_review", show=False),
    ]

    def __init__(self, directory: Path, debug: bool = False) -> None:
        super().__init__()
        self.directory = directory
        self.rules = RenameRules()
        self.files: list[Path] = []
        self.table_view = TableView()
        self._visible: list[int] = []
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
        self._last_result = ""
        self._rebuilding = False
        self._rebuild_depth = 0
        self._scroll_restore_gen = 0
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
        self.scan_backend: ScanBackend | None = None
        self.scan_pending: dict[str, PendingScan] = {}
        self.scan_suffix: dict[str, list[str]] = {}
        self.scan_accepted: dict[str, list[str]] = {}
        self.scan_sessions: dict[str, ScanSession] = {}
        self._date_plan: DatePlan | None = None
        self._date_plan_key: tuple[tuple[Path, ...], tzinfo | None] | None = None
        self._name_plan_cache: tuple[object, list[str], list[str]] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="timeline")
        with Horizontal(id="body"):
            yield FileTable(id="files")
            yield DetailPane(id="detail")
        yield Button("Filter and sort", id="view-open", compact=True)
        yield Static("", id="earliest-hint", markup=False)
        yield OptionsPane(id="options")
        with Vertical(id="batch-bar"):
            yield Static("", id="pending-summary", markup=False)
            with WrappingRow(id="opt-actions"):
                yield Button(
                    "Enter Apply", id="act-apply", compact=True, tooltip=BUTTON_TIPS["act-apply"]
                )
                yield Button("u Undo", id="act-undo", compact=True, tooltip="Session undo only")
                yield Button(
                    "Last result",
                    id="act-result",
                    compact=True,
                    tooltip="Inspect successes, failures and undo coverage",
                )
        yield Input(placeholder="find", id="find", select_on_focus=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#files").border_title = "1 Files"
        self.query_one("#detail").border_title = "2 Detail"
        self.query_one("#options").border_title = "3 Options"
        self.query_one("#preview-slot").border_title = "4 Preview"
        saved = load_view()
        self.preview_on = saved.preview
        self.query_one("#preview-slot").set_class(not saved.preview, "-off")
        self._set_zoom(saved.zoom, persist=False)
        self.rules.datetime_stamp = filename_clock_text(datetime.now())
        self.reload_files()
        if not self.exiftool:
            self.set_status(
                "exiftool is not on PATH. Video, HEIC, and PNG metadata writes are off."
            )
        self.refresh_view()
        self._apply_layout(self.size.width)
        self.call_after_refresh(self._fit_table_columns)
        self.set_interval(0.5, self._tick)
        self.query_one(FileTable).focus()

    def on_unmount(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self.journal.cleanup()

    def on_resize(self, event: object) -> None:
        size = getattr(event, "size", self.size)
        if self.is_mounted:
            self._apply_layout(size.width, size.height)
            if not self._rebuilding:
                self.refresh_view()
            self.call_after_refresh(self._fit_table_columns)

    def _apply_layout(self, width: int, height: int | None = None) -> None:
        body = self.query_one("#body")
        files = self.query_one("#files")
        detail = self.query_one("#detail")
        narrow = width < 100
        compact = narrow and (self.size.height if height is None else height) < 32
        self.screen.set_class(compact, "-compact")
        files.border_subtitle = "2 Detail · 4 Preview" if compact else ""
        body.set_class(narrow, "-narrow")
        if narrow:
            body.styles.layout = "vertical"
            files.styles.width = "100%"
            detail.styles.width = "100%"
            files.styles.height = "2fr"
            detail.styles.height = "1fr"
        else:
            body.styles.layout = "horizontal"
            files.styles.width = "2fr"
            detail.styles.width = "1fr"
            files.styles.height = "100%"
            detail.styles.height = "100%"

    def _tick(self) -> None:
        if linux_stdin_deleted():
            self.exit()
            return
        if not self.rules.add_datetime or len(self.screen_stack) > 1:
            return
        stamp = filename_clock_text(datetime.now())
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
        self._visible = list(range(len(self.files)))
        self.selected.clear()
        self.locked.clear()
        self.manual.clear()
        self._facts.clear()
        self._date_plan = None
        self._date_plan_key = None
        self._cursor = 0
        self.visual = False
        self.anchor = None
        alive = {path.resolve() for path in self.files}
        self.scan_pending = {
            key: item for key, item in self.scan_pending.items() if item.path.resolve() in alive
        }
        names = {str(path) for path in self.files}
        self.scan_suffix = {key: parts for key, parts in self.scan_suffix.items() if key in names}
        self.scan_accepted = {
            key: parts for key, parts in self.scan_accepted.items() if key in names
        }
        self.scan_sessions = {key: item for key, item in self.scan_sessions.items() if key in names}

    def target_zone(self) -> tzinfo:
        return resolve_tz(self.rules.source_tz)

    def plan_zone(self) -> tzinfo | None:
        if self.rules.source_tz:
            return resolve_tz(self.rules.source_tz)
        return None

    def cached_date_plan(self) -> DatePlan:
        key = (tuple(self.files), self.plan_zone())
        if self._date_plan is None or self._date_plan_key != key:
            self._date_plan = plan_filename_dates(self.files, self.plan_zone())
            self._date_plan_key = key
        return self._date_plan

    def facts_for(self, path: Path) -> FileFacts:
        key = (str(path), self.rules.source_tz, self.rules.use_json_data)
        cached = self._facts.get(key)
        if cached is None:
            cached = inspect_file(path, self.rules, self.target_zone())
            self._facts[key] = cached
        return cached

    def preview_for(self, index: int) -> str:
        _proposed, resolved = self._name_plan()
        return resolved[index]

    def _sibling_stems(self, index: int) -> list[str]:
        return [
            os.path.splitext(self.files[other].name)[0]
            for other in range(len(self.files))
            if other != index
        ]

    def _with_collision_base(self, filename: str, index: int) -> str:
        stem, ext = os.path.splitext(filename)
        base = collision_stem(stem, self._sibling_stems(index))
        if base == stem:
            return filename
        return f"{base}{ext}" if base else filename

    def _names_key(self) -> tuple[object, ...]:
        rules = self.rules
        fact_tokens = []
        for path in self.files:
            facts = self.facts_for(path)
            candidate = earliest_date(facts)
            instant = candidate.instant.isoformat() if candidate and candidate.instant else ""
            fact_tokens.append((facts.modtime_text, facts.origtime_text, facts.geodata, instant))
        return (
            tuple(self.files),
            tuple(self.locked.items()),
            tuple(self.manual.items()),
            tuple((key, tuple(value)) for key, value in self.scan_suffix.items()),
            tuple((key, tuple(value)) for key, value in self.scan_accepted.items()),
            id(self.facts_for),
            tuple(fact_tokens),
            rules.use_earliest,
            rules.add_datetime,
            rules.add_modtime,
            rules.add_origtime,
            rules.use_json_data,
            rules.add_geodata,
            rules.use_prefix,
            rules.strip_original_name,
            rules.strip_to_text,
            rules.remove_caps,
            rules.remove_special,
            rules.replace_spaces,
            rules.replace_dots,
            rules.source_tz,
            rules.datetime_stamp,
        )

    def _proposed_name(self, index: int) -> str:
        path = self.files[index]
        if index in self.locked:
            return self._with_collision_base(self.locked[index], index)
        key = str(path)
        accepted = self.scan_accepted.get(key, [])
        parts = self._formatted_scan_parts(path)
        siblings = self._sibling_stems(index)
        if index in self.manual:
            return apply_scan_suffix(
                self._with_collision_base(self.manual[index], index),
                parts,
                accepted=accepted,
            )
        file_stem, _ext = os.path.splitext(path.name)
        bare = strip_accepted_suffix(file_stem, accepted)
        stem, _ext = preview_base(
            path.name,
            self.facts_for(path),
            self.rules,
            stem=bare,
            sibling_stems=siblings,
        )
        return apply_scan_suffix(
            path.name,
            parts,
            accepted=[],
            stem=stem if stem else "",
        )

    def _name_plan(self) -> tuple[list[str], list[str]]:
        key = self._names_key()
        cached = self._name_plan_cache
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        proposed = [self._proposed_name(index) for index in range(len(self.files))]
        resolved = assign_unique_names(self.files, proposed)
        self._name_plan_cache = (key, proposed, resolved)
        return proposed, resolved

    def refresh_view(self) -> None:
        self._recompute_changing()
        self._recompute_neighbors()
        self._recompute_visible()
        self._fill_table()
        self._sync_options()
        self._update_detail()
        self._update_timeline()
        self._update_status()
        self._update_subtitle()

    def action_filter_sort(self) -> None:
        if self._find_open:
            return

        def done(view: TableView | None) -> None:
            if view is None:
                return
            self.table_view = view
            self.visual = False
            self.anchor = None
            self._search_nav = False
            self._preview_gen += 1
            # The old viewport belongs to a different display order.
            self.query_one(FileTable).scroll_to(y=0, animate=False)
            self.refresh_view()
            self.call_after_refresh(self._move_to, self._cursor)

        self.push_screen(FilterSortScreen(self.table_view), done)

    def _recompute_visible(self) -> None:
        view = self.table_view
        visible = []
        captures = {}
        warnings = {}
        for index, path in enumerate(self.files):
            facts = self.facts_for(path)
            warning = is_warning(facts, self._neighbor_at(index))
            pending = self.scan_pending.get(str(path))
            if not matches_view(
                view,
                path.suffix,
                facts,
                warning=warning,
                changed=self.preview_for(index) != path.name,
                pending=pending is not None
                and (pending.moment is not None or bool(pending.description)),
            ):
                continue
            visible.append(index)
            captures[index] = embedded_capture(facts)
            warnings[index] = warning
        # Stable name ties in either direction, with absent capture dates always last.
        # New-name ties use the current filename. self.files stays in folder order.
        if view.sort == "new":
            visible.sort(
                key=lambda index: (
                    self.preview_for(index).lower(),
                    self.files[index].name.lower(),
                ),
                reverse=view.descending,
            )
        else:
            visible.sort(
                key=lambda index: self.files[index].name.lower(),
                reverse=view.descending,
            )
        if view.sort == "capture":
            dated = [index for index in visible if captures[index] is not None]
            dated.sort(key=lambda index: captures[index], reverse=view.descending)
            visible = dated + [index for index in visible if captures[index] is None]
        elif view.sort == "warning":
            visible.sort(key=lambda index: not warnings[index], reverse=view.descending)
        self._visible = visible
        if self.visual and (self.anchor not in visible or self._cursor not in visible):
            self.visual = False
            self.anchor = None
        if self._cursor not in visible:
            self._cursor = visible[0] if visible else 0

    def _recompute_changing(self) -> None:
        """Indexes whose apply preview differs from the file as it is now."""
        self._changing = set()
        if not self.files:
            return
        if self.date_mode:
            plan = self.cached_date_plan()
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

    def _fit_code(self, index: int, path: Path) -> str:
        if self.date_mode:
            plan = self.cached_date_plan()
            todo = {item.path for item in plan.todo}
            same = {item.path for item in plan.same}
            if path in todo:
                return "upd"
            if path in same:
                return "ok"
            return "err"
        if self.mtime_mode:
            _label, moment = mtime_source(self.facts_for(path), self.rules, _local_zone())
            if moment is None:
                return "ok"
            try:
                current = path.stat().st_mtime
            except OSError:
                return "upd"
            if abs(current - moment.timestamp()) < 1:
                return "ok"
            return "upd"
        proposed, resolved = self._name_plan()
        if resolved[index] != proposed[index]:
            return "dup"
        if resolved[index] == path.name:
            return "ok"
        return "rn"

    def _mark_cells(self, index: int, path: Path) -> tuple[Text, Text, Text]:
        facts = self.facts_for(path)
        source = Text(self._fit_code(index, path))
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
        return STATUS_LABEL.get(status, status)

    def _status_code(self, index: int) -> str:
        path = self.files[index]
        if self.date_mode:
            plan = self.cached_date_plan()
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
        return f"Date source: {source}. Warning: {flag_name}. Missing: {missing}. State: {stat}."

    def _update_timeline(self) -> None:
        widget = self.query_one("#timeline", Static)
        width = widget.size.width or self.size.width
        if width <= 0:
            width = 80
        instants = [self.facts_for(path).winner_instant for path in self.files]
        highlight = self._cursor if self._visible else None
        line = timeline_bins(instants, width, highlight)
        if not line:
            widget.update(Text("no dates", style="dim"))
        else:
            widget.update(line)

    def _flex_budget(self, fixed_content: int, column_count: int) -> int:
        """Cells left for flexible columns after the short ones and padding."""
        table = self.query_one(FileTable)
        viewport = table.scrollable_content_region.width
        if viewport <= 0:
            viewport = max(table.size.width - 2, 64)
        pad = 2 * table.cell_padding * column_count
        return viewport - fixed_content - pad

    def _flex_width(self, fixed_content: int, column_count: int, flex_count: int) -> int:
        """Content width for each flexible column so the row fits the pane."""
        remaining = self._flex_budget(fixed_content, column_count)
        if flex_count <= 0:
            return 8
        return max(8, remaining // flex_count)

    def _name_new_widths(self) -> tuple[int, int]:
        """Split leftover width across Name and New. Show full names when they fit."""
        remaining = max(16, self._flex_budget(25, 7))
        name_need = max((len(path.name) for path in self.files), default=8)
        new_need = max(
            (len(self.preview_for(index)) for index in range(len(self.files))),
            default=8,
        )
        name_need = max(8, name_need)
        new_need = max(8, new_need)
        name_w = min(name_need, remaining // 2)
        new_w = min(new_need, remaining - name_w)
        leftover = remaining - name_w - new_w
        name_gap = name_need - name_w
        new_gap = new_need - new_w
        if leftover and (name_gap or new_gap):
            if name_gap and new_gap:
                extra = leftover * name_gap // (name_gap + new_gap)
                name_w += extra
                new_w += leftover - extra
            elif name_gap:
                name_w += leftover
            else:
                new_w += leftover
        elif leftover:
            extra = leftover // 2
            name_w += extra
            new_w += leftover - extra
        return max(8, name_w), max(8, new_w)

    def _desired_flex(self) -> tuple[int, int]:
        if self.date_mode:
            return self._flex_width(63, 8, 1), 19
        if self.mtime_mode:
            return self._flex_width(44, 7, 1), 19
        return self._name_new_widths()

    def _fit_table_columns(self) -> None:
        """Rebuild name columns from the pane's current width after layout."""
        if not self.is_mounted or self._rebuilding or self._syncing:
            return
        try:
            table = self.query_one(FileTable)
        except NoMatches:
            return
        if table.row_count and self._visible[table.cursor_row] != self._cursor:
            # Let the queued highlight update selection before rebuilding columns.
            return
        name_w, new_w = self._desired_flex()
        if name_w == self._name_width and new_w == self._new_width:
            return
        self._fill_table()

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
        names = [(self.files[index].name, self.preview_for(index)) for index in self._visible]
        return [self._visible[row] for row in query_hits(names, needle)]

    def _column_keys(self) -> tuple[str, ...]:
        if self.date_mode:
            return ("mark", "fit", "flag", "miss", "file", "cur", "new", "status")
        if self.mtime_mode:
            return ("mark", "fit", "flag", "miss", "name", "mtime", "status")
        return ("mark", "fit", "flag", "miss", "name", "new", "status")

    def _current_rows(self) -> list[tuple[object, ...]]:
        if self.date_mode:
            return self._date_rows()
        if self.mtime_mode:
            return self._mtime_rows()
        return self._rename_rows()

    def _begin_rebuild(self) -> None:
        """Ignore table highlights until the rebuild's cursor events have run."""
        self._rebuild_depth += 1
        self._rebuilding = True

    def _end_rebuild(self) -> None:
        self._rebuild_depth -= 1
        if self._rebuild_depth <= 0:
            self._rebuild_depth = 0
            self._rebuilding = False
            try:
                table = self.query_one(FileTable)
            except NoMatches:
                return
            if table.row_count and self._visible[table.cursor_row] != self._cursor:
                key = table.ordered_rows[table.cursor_row].key
                self.on_data_table_row_highlighted(
                    DataTable.RowHighlighted(table, table.cursor_row, key)
                )

    def _restore_table_scroll(self, x: float, y: float, gen: int) -> None:
        if gen != self._scroll_restore_gen:
            return
        try:
            table = self.query_one(FileTable)
        except NoMatches:
            return
        table.scroll_to(x, y, animate=False)

    def _fill_table(self) -> None:
        table = self.query_one(FileTable)
        scroll_x = table.scroll_x
        scroll_y = table.scroll_y
        self._scroll_restore_gen += 1
        gen = self._scroll_restore_gen
        self._begin_rebuild()
        table.clear(columns=True)
        if self.date_mode:
            self._add_date_columns(table)
        elif self.mtime_mode:
            self._add_mtime_columns(table)
        else:
            self._add_rename_columns(table)
        rows = self._current_rows()
        for index in self._visible:
            table.add_row(*rows[index], key=str(index))
        if table.row_count:
            table.move_cursor(row=self._visible.index(self._cursor), scroll=False)
        else:
            self._cursor = 0
        # Skip a no-op restore of (0, 0). A deferred one can wipe a later scroll.
        if scroll_x or scroll_y:
            table.scroll_to(scroll_x, scroll_y, animate=False)
            # After the cursor watcher scrolls the row into view.
            table.call_after_refresh(self._restore_table_scroll, scroll_x, scroll_y, gen)
        self.call_after_refresh(self._end_rebuild)

    def _paint_rows(self, indexes: Iterable[int]) -> None:
        """Update selection or lock cells without clearing the table."""
        if not self.is_mounted:
            return
        previous_visible = self._visible[:]
        self._recompute_changing()
        self._recompute_visible()
        if self._visible != previous_visible:
            self.visual = False
            self.anchor = None
            self.refresh_view()
            return
        wanted = sorted({index for index in indexes if 0 <= index < len(self.files)})
        try:
            table = self.query_one(FileTable)
        except NoMatches:
            return
        if wanted and table.row_count == len(self._visible):
            rows = self._current_rows()
            columns = self._column_keys()
            for index in wanted:
                if index not in self._visible:
                    continue
                for column, value in zip(columns, rows[index], strict=True):
                    table.update_cell(str(index), column, value, update_width=False)
        elif wanted:
            self.refresh_view()
            return
        self._refresh_side_panels()

    def _add_rename_columns(self, table: FileTable) -> None:
        name_w, new_w = self._name_new_widths()
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Fit", key="fit", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("Name", key="name", width=name_w)
        table.add_column("New", key="new", width=new_w)
        table.add_column("State", key="status", width=11)
        self._name_width = name_w
        self._new_width = new_w

    def _add_date_columns(self, table: FileTable) -> None:
        width = self._flex_width(fixed_content=63, column_count=8, flex_count=1)
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Fit", key="fit", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("File", key="file", width=width)
        table.add_column("Current", key="cur", width=19)
        table.add_column("New", key="new", width=19)
        table.add_column("State", key="status", width=11)
        self._name_width = width
        self._new_width = 19

    def _add_mtime_columns(self, table: FileTable) -> None:
        width = self._flex_width(fixed_content=44, column_count=7, flex_count=1)
        table.add_column("Sel", key="mark", width=3)
        table.add_column("Fit", key="fit", width=3)
        table.add_column("Flag", key="flag", width=4)
        table.add_column("Miss", key="miss", width=4)
        table.add_column("Name", key="name", width=width)
        table.add_column("New mtime", key="mtime", width=19)
        table.add_column("State", key="status", width=11)
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
        plan = self.cached_date_plan()
        by_path = {item.path: item for item in plan.todo + plan.same}
        zone = self.plan_zone()
        rows: list[tuple[str, Text, Text, Text, Text, str, Text, str]] = []
        for index, path in enumerate(self.files):
            item = by_path.get(path)
            current = self._clock(path, zone)
            if item is None:
                parsed, _precision = parse_filename_datetime(path.name)
                new_str = "---" if parsed is None else "Error"
                status = "NO DATE" if parsed is None else "ERR"
                new_cell = self._styled(new_str, self._new_width, "dim")
            else:
                # `changing` flag is passed as a keyword argument to keep the
                # line under the 100‑char limit and improve readability.
                new_cell = _format_date_diff(
                    item.cur, item.target, zone, changing=item in plan.todo
                )
                status = "UPDATE" if item in plan.todo else "UNCHANGED"
            if index in self.selected:
                status = "SELECTED"
            file_style = "" if index in self._changing else "dim"
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
                    new_cell,
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
        if not self.is_mounted:
            return
        self.query_one("#earliest-hint", Static).update(self._earliest_hint())
        try:
            self.query_one("#detail-body", Static).update(self._detail_text())
        except NoMatches:
            return
        self._schedule_preview()

    def _earliest_hint(self) -> str:
        if not self._visible:
            return "No date available"
        facts = self.facts_for(self.files[self._cursor])
        candidate = earliest_date(facts)
        if candidate is None:
            return "Earliest for this file · No date available"
        moment = candidate.instant.astimezone(self.target_zone())
        source = earliest_source(candidate)
        hint = f"Earliest for this file · {moment:%Y-%m-%d %H:%M} · {source}"
        chosen = []
        if self.rules.add_origtime and facts.origtime:
            chosen.append(facts.origtime)
        if self.rules.add_modtime and facts.modtime:
            chosen.append(facts.modtime)
        if self.rules.add_datetime:
            _label, today = mtime_source(
                facts,
                RenameRules(add_datetime=True, datetime_stamp=self.rules.datetime_stamp),
                _local_zone(),
            )
            if today is not None:
                chosen.append(today)
        if any(date > candidate.instant for date in chosen):
            hint += f". {source} is earlier. Choose ‘Use earliest date’."
        return hint

    def _detail_text(self) -> Text:
        text = Text()
        if not self._visible:
            if self.files:
                return Text(
                    "No files match these filters. Reset Filter and sort to show all files.\n"
                )
            text.append("No files in this folder.\n")
            text.append("Press r after you add some.\n", style="dim")
            return text
        path = self.files[self._cursor]
        facts = self.facts_for(path)
        new_name = self.preview_for(self._cursor)
        text.append(path.name + "\n", style="bold")
        text.append(f"Before: {path.name}\n")
        text.append(f"After:  {new_name}\n", style="bold")
        if self._cursor in self.locked:
            text.append("locked\n", style="green")
        elif self._cursor in self.manual:
            text.append("manual edit\n", style="yellow")
        for tag, piece in name_diff_ops(path.name, new_name):
            style = {"equal": "", "delete": "red strike", "insert": "green"}[tag]
            text.append(piece, style=style)
        text.append("\n\n")
        text.append(self._mark_sentence() + "\n")
        if facts.json_error:
            text.append("JSON sidecar could not be read.\n", style="red")
        if facts.sidecar is not None:
            text.append(f"sidecar  {facts.sidecar.name}\n")
        text.append("Date sources · file times are not confirmed capture dates\n", style="bold")
        if facts.candidates:
            for candidate in facts.candidates:
                text.append(f"  {candidate.label}\n    {candidate.text}\n")
        else:
            text.append("  none\n", style="dim")
        if self.date_mode:
            plan = self.cached_date_plan()
            by_path = {item.path: item for item in plan.todo + plan.same}
            item = by_path.get(path)
            if item is not None:
                text.append("\nFilename Date Target\n", style="bold")
                zone = self.plan_zone()
                target_str = _format_stamp(item.target, zone)
                text.append(f"  target  {target_str}\n")
                summary = _format_diff_summary(item.cur, item.target, item.prec, item.nudged)
                text.append(f"  diff    {summary}\n")
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
        text.append(
            "\nTable codes: Fit = ok, rn, or dup for names; ok, upd, or err for filename dates; "
            "ok or upd for file times. Flag = warning. Miss = missing metadata.\n",
            style="dim",
        )
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
        if not self._visible:
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

    def _update_batch_bar(self) -> None:
        visible = {self.files[index] for index in self._visible}
        hint = "Previews alone do not queue renames."
        if self.date_mode:
            paths = (
                [self.files[index] for index in sorted(self.selected) if index < len(self.files)]
                if self.selected
                else list(self.files)
            )
            plan = plan_filename_dates(paths, self.plan_zone())
            targets = {item.path for item in plan.todo}
            summary = f"Apply filename dates: {len(targets)} files"
            if self.scan_pending or self.scan_suffix or self.manual or self.locked:
                hint = "Name/photo-review work waits for rename mode."
        elif self.mtime_mode:
            targets = {
                self.files[index]
                for index in self.selected
                if index < len(self.files)
                and mtime_source(self.facts_for(self.files[index]), self.rules, _local_zone())[1]
                is not None
            }
            summary = f"Apply file times: {len(targets)} files"
            if self.scan_pending or self.scan_suffix or self.manual or self.locked:
                hint = "Name/photo-review work waits for rename mode."
        else:
            pairs = [(src, name) for src, name in self._rename_pairs() if src.name != name]
            pending = list(self.scan_pending.values())
            targets = {src for src, _name in pairs} | {item.path for item in pending}
            metadata = sum(
                1
                for item in pending
                if (
                    item.moment is not None
                    and (item.overwrite or not self.facts_for(item.path).has_exif)
                )
                or (item.description and item.path.suffix.lower() in IMAGE_EXTENSIONS)
            )
            notes = sum(1 for path in targets if str(path) in self.scan_sessions)
            summary = f"Apply: {len(pairs)} renames · {metadata} metadata · {notes} notes"
        hidden = len(targets - visible)
        selected_hidden = len(self.selected.difference(self._visible))
        summary += (
            f"\nHidden targets: {hidden} · Selection: {len(self.selected)} "
            f"({selected_hidden} hidden)"
            f"\n{hint}"
        )
        self.query_one("#pending-summary", Static).update(summary)
        label = (
            "Apply filename dates"
            if self.date_mode
            else "Apply file times"
            if self.mtime_mode
            else "Apply names / notes"
        )
        self.query_one("#act-apply", Button).label = f"Enter {label}"
        undo = self.query_one("#act-undo", Button)
        last = self.journal.batches[-1] if self.journal.batches else None
        undo.label = f"u Undo {BATCH_LABEL.get(last.kind, last.kind)}" if last else "u Undo"
        undo.disabled = last is None
        undo.tooltip = self._undo_coverage(last)
        self.query_one("#act-result", Button).disabled = not self._last_result

    def _undo_coverage(self, batch: Batch | None) -> str:
        if batch is None or not batch.ops:
            return "No undo entry was created for this action."
        lines = [f"Session undo: {BATCH_LABEL.get(batch.kind, batch.kind)}. Available until quit."]
        lines.extend(batch.notes)
        for op in batch.ops:
            if isinstance(op, MetaOp) and op.copy_failed:
                lines.append(
                    f"{op.path.name}: metadata backup failed; only file times can be restored."
                )
        lines.append(
            "Undo restores recorded rename steps, file times and available metadata backups."
        )
        return "\n".join(lines)

    def _record_result(self, message: str, details: list[str], batch: Batch | None = None) -> None:
        self._last_result = message + "\n\n" + "\n".join(details)
        self._last_result += "\n\n" + self._undo_coverage(batch)
        if batch is not None and not batch.ops:
            self._last_result += "\n" + "\n".join(batch.notes)
        self.set_status(message + " Open Last result for details and undo coverage.")

    def action_last_result(self) -> None:
        if self._last_result:
            self.push_screen(ResultScreen(self._last_result))
        else:
            self.set_status("No batch result in this session yet.")
            self._update_status()

    def _update_subtitle(self) -> None:
        if self.visual:
            mode = "RANGE SELECT"
        elif self.date_mode:
            mode = "FILENAME DATE"
        elif self.mtime_mode:
            mode = "SET MTIME"
        else:
            mode = "RENAME"
        if self._find_open:
            mode = "FIND · Enter keeps match · Esc returns"
        total = len(self.files)
        pos = self._visible.index(self._cursor) + 1 if self._visible else 0
        place = "Prefix" if self.rules.use_prefix else "Suffix"
        zone = self.rules.source_tz or "Local"
        extra = []
        if self.selected:
            extra.append(f"Sel:{len(self.selected)}")
        if self.locked:
            extra.append(f"Lock:{len(self.locked)}")
        if self.manual:
            extra.append(f"Man:{len(self.manual)}")
        hidden_selected = len(self.selected.difference(self._visible))
        if hidden_selected:
            extra.append(f"Hidden selected:{hidden_selected}")
        tail = ("  " + " ".join(extra)) if extra else ""
        changing = len(self._changing)
        count = f"{changing} of {total} previews differ"
        warnings = sum(
            1
            for index, file_path in enumerate(self.files)
            if is_warning(self.facts_for(file_path), self._neighbor_at(index))
        )
        warning = f"  W:{warnings}" if warnings else ""
        found = ""
        if self._find_open or self._search_active:
            needle = self._draft if self._find_open else self._search_query
            hits = self._hit_indexes(needle)
            found = "  " + find_label(
                self._visible.index(self._cursor) if self._visible else -1,
                [self._visible.index(index) for index in hits],
            )
        self.sub_title = (
            f"{mode}  {pos}/{len(self._visible)} shown/{total} total  {place}  {zone}  "
            f"{count}{warning}{found}{tail}"
        )
        view = self.table_view
        filters = [label for key, label in FILTERS if key in view.filters]
        if view.extension:
            filters.append(view.extension)
        label = ", ".join(filters) if filters else "All files"
        order = next(label for label, key in SORTS if key == view.sort)
        direction = "descending" if view.descending else "ascending"
        hidden = f"; {hidden_selected} hidden selected" if hidden_selected else ""
        summary = f"{len(self._visible)}/{total} shown{hidden} | {label} | {order}, {direction}"
        button = self.query_one("#view-open", Button)
        button.label = f"Filter and sort: {summary}"
        button.tooltip = summary
        self._update_batch_bar()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._rebuilding or not self.is_mounted or not event.data_table.is_attached:
            return
        if event.cursor_row >= len(self._visible):
            return
        table = event.data_table
        if event.cursor_row != table.cursor_row:
            return
        if event.row_key != table.ordered_rows[table.cursor_row].key:
            return
        row = self._visible[event.cursor_row]
        if self._search_nav:
            if row != self._cursor:
                try:
                    table = self.query_one(FileTable)
                except NoMatches:
                    return
                if table.row_count:
                    table.move_cursor(row=self._visible.index(self._cursor))
                return
            self._search_nav = False
            self._refresh_side_panels()
            return
        if self.visual and self.anchor is not None and row != self._cursor:
            old = self._cursor
            anchor = self._visible.index(self.anchor)
            old_pos = self._visible.index(old)
            new_pos = self._visible.index(row)
            old_block = set(self._visible[min(anchor, old_pos) : max(anchor, old_pos) + 1])
            new_block = set(self._visible[min(anchor, new_pos) : max(anchor, new_pos) + 1])
            self.selected.update(new_block - old_block)
            self.selected.difference_update(old_block - new_block)
            self._cursor = row
            self._paint_rows(old_block.symmetric_difference(new_block))
            return
        self._cursor = row
        self._refresh_side_panels()

    def _refresh_side_panels(self) -> None:
        if not self.is_mounted:
            return
        self._sync_options()
        self._update_detail()
        self._update_timeline()
        self._update_status()
        self._update_subtitle()
        self.call_after_refresh(self._fit_table_columns)

    def _move_to(self, row: int) -> None:
        if row not in self._visible:
            return
        table = self.query_one(FileTable)
        self._cursor = row
        # An explicit navigation supersedes deferred scrolls from a table rebuild.
        self._scroll_restore_gen += 1
        if table.row_count:
            table.move_cursor(row=self._visible.index(row))

    def _targets(self) -> list[int]:
        if self.selected:
            return sorted(index for index in self.selected if index < len(self.files))
        if self._visible and self._cursor < len(self.files):
            return [self._cursor]
        return []

    def _hidden_scope(self, paths: Iterable[Path]) -> str:
        visible = {self.files[index] for index in self._visible}
        hidden = sorted({path.name for path in paths if path not in visible})
        if not hidden:
            return ""
        return "\nIncludes hidden files (" + str(len(hidden)) + "): " + ", ".join(hidden) + "\n"

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
        if self.visual:
            self.visual = False
            self.anchor = None
            self.set_status("Visual select off")
            self._paint_rows([])
            return
        if self._cancel_modes_on_escape():
            self.refresh_view()

    def _set_zoom(self, which: str | None, *, persist: bool = True) -> None:
        self._zoom = which
        for name in ("files", "detail", "options", "preview"):
            self.screen.set_class(which == name, f"-zoom-{name}")
        if persist:
            self._save_view()
        self.call_after_refresh(self._fit_table_columns)

    def _save_view(self) -> None:
        save_view(ViewState(preview=self.preview_on, zoom=self._zoom))

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
            if self._visible:
                self._move_to(self._visible[0])
            self.set_status("")
            self._update_status()
        else:
            self._leader = True
            self.set_status("g then g: first file")
            self._update_status()

    def action_goto_top(self) -> None:
        self._begin()
        if self._visible:
            self._move_to(self._visible[0])

    def action_goto_end(self) -> None:
        self._begin()
        if self._visible:
            self._move_to(self._visible[-1])

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
        self._save_view()

    def action_reset_view(self) -> None:
        self._begin()
        clear_view()
        self.preview_on = True
        self.query_one("#preview-slot").set_class(False, "-off")
        self._set_zoom(None, persist=False)
        self._preview_gen += 1
        self._schedule_preview()
        self.set_status("Preview and zoom reset. Filters and staged work kept.")
        self._sync_options()
        self._update_status()
        self.query_one(FileTable).focus()

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
        if not self._visible:
            self._update_subtitle()
            self._update_status()
            return
        hits = self._hit_indexes(query)
        if query and hits:
            positions = {index: row for row, index in enumerate(self._visible)}
            origin = positions.get(self._find_origin, 0)
            target = next((index for index in hits if positions[index] >= origin), hits[0])
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
        if not self._visible:
            self._preview_gen += 1
            self.query("#preview-image").remove()
            self.query_one("#preview-caption", Static).update("No file selected")
            self.query_one("#preview-fallback", Static).update("")
            return
        self._preview_timer = self.set_timer(file_preview.DEBOUNCE_SECONDS, self._kick_preview)

    def _kick_preview(self) -> None:
        self._preview_timer = None
        if not self.is_running or not self.preview_on or not self._visible:
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
        return bool(self._visible) and str(self.files[self._cursor]) == path_str

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

    def action_prev_warning(self) -> None:
        hits = [
            index
            for index, path in enumerate(self.files)
            if is_warning(self.facts_for(path), self._neighbor_at(index))
        ]
        self._jump_hits(hits, -1, "No warnings.", "No other warning.")

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
        positions = {index: row for row, index in enumerate(self._visible)}
        hit_set = set(hits)
        hits = [index for index in self._visible if index in hit_set]
        if not hits:
            self.set_status(none_msg)
            self._update_status()
            return
        current = positions.get(self._cursor, -1)
        if step > 0:
            later = [index for index in hits if positions[index] > current]
            target = later[0] if later else hits[0]
        else:
            earlier = [index for index in hits if positions[index] < current]
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
            self._paint_rows([self._cursor])
            return
        if not self._visible:
            return
        if self._cursor in self.selected:
            self.selected.remove(self._cursor)
        else:
            self.selected.add(self._cursor)
        self._paint_rows([self._cursor])

    def action_visual(self) -> None:
        self._begin()
        self.visual = not self.visual
        if self.visual and self._visible:
            self.anchor = self._cursor
            self.selected.add(self._cursor)
            self.set_status("Visual select on")
            self._paint_rows([self._cursor])
            return
        self.anchor = None
        self.visual = False
        self.set_status("Visual select off")
        self._paint_rows([])

    def action_select_all(self) -> None:
        self._begin()
        self.selected.update(self._visible)
        self.set_status(f"Selected all {len(self._visible)} visible files")
        self._paint_rows(self.selected)

    def action_select_none(self) -> None:
        self._begin()
        previous = set(self.selected)
        self.selected.clear()
        self.visual = False
        self.anchor = None
        self.set_status("Selection cleared, including hidden files. Staged edits kept.")
        self._paint_rows(previous)

    def action_toggle(self, field: str) -> None:
        self._begin()
        self._set_rule(field, not getattr(self.rules, field))

    def _set_rule(self, field: str, value: bool) -> None:
        if value and field == "use_earliest":
            for manual in ("add_datetime", "add_modtime", "add_origtime"):
                setattr(self.rules, manual, False)
        elif value and field in {"add_datetime", "add_modtime", "add_origtime"}:
            self.rules.use_earliest = False
        setattr(self.rules, field, value)
        if field == "use_json_data":
            self._facts.clear()
            state = "on" if self.rules.use_json_data else "off"
            self.set_status(f"JSON data is {state}")
        if field == "add_datetime" and self.rules.add_datetime:
            self.rules.datetime_stamp = filename_clock_text(datetime.now())
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
            self.query_one("#opt-use_earliest", Button).label = (
                "E Use earliest date · ON" if self.rules.use_earliest else "E Use earliest date"
            )
            zone = self.rules.source_tz or "Local"
            self.query_one("#act-zone", Button).label = f"T {zone}"
            cleanup = [
                label for _key, field, label in OPTION_FIELDS[6:] if getattr(self.rules, field)
            ]
            self.query_one("#group-cleanup", Collapsible).title = "Filename cleanup · " + (
                ", ".join(cleanup) if cleanup else "off"
            )
            mode = (
                "dates from filenames ON"
                if self.date_mode
                else "file times ON"
                if self.mtime_mode
                else "no write mode"
            )
            self.query_one(
                "#group-metadata", Collapsible
            ).title = f"Metadata and file times · {mode}"
            self.query_one(
                "#group-staging", Collapsible
            ).title = (
                f"Staged edits and selection · {len(self.manual)} manual, {len(self.locked)} locked"
            )
            self.query_one(
                "#group-view", Collapsible
            ).title = f"View and session · preview {'ON' if self.preview_on else 'off'}"
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
        if event.button.id == "opt-use_earliest":
            self.action_toggle("use_earliest")
            return
        run = {
            "act-apply": "apply",
            "act-undo": "undo",
            "act-zone": "timezone",
            "act-write-zone": "write_timezone",
            "view-open": "filter_sort",
            "act-gps": "write_geodata",
            "act-refresh": "reload_files",
            "act-reset": "reset_rules",
            "act-scan": "scan_review",
            "act-edit": "insert",
            "act-lock": "lock",
            "act-lock-selected": "lock_selected",
            "act-unlock": "unlock_all",
            "act-select-all": "select_all",
            "act-clear-selection": "select_none",
            "act-discard-name": "discard_manual_name",
            "act-reset-view": "reset_view",
            "act-result": "last_result",
        }.get(event.button.id or "")
        if run:
            getattr(self, f"action_{run}")()

    def action_scan_review(self) -> None:
        self._begin()
        if self._find_open or not self._visible:
            return
        start = self.files[self._cursor]
        settings = load_scan_settings()
        backend = self.scan_backend or OllamaBackend(host=settings.host)
        facts = self.facts_for(start)

        def done(result: StageResult | None) -> None:
            if result is None:
                return
            self.stage_scan_review(result, refresh=True, closing=True)

        stem, ext = self._ordinary_stem_ext(self._cursor)
        key = str(start)
        self.push_screen(
            ReviewScreen(
                self.files,
                start=start,
                backend=backend,
                settings=settings,
                zone=self.target_zone(),
                has_capture=facts.has_exif,
                base_stem=stem,
                base_ext=ext,
                locked=self._cursor in self.locked,
                accepted_suffix=list(self.scan_accepted.get(key, [])),
                session=self.scan_sessions.get(key),
            ),
            done,
        )

    def stage_scan_review(
        self,
        result: StageResult,
        *,
        refresh: bool = True,
        closing: bool = False,
    ) -> None:
        key = str(result.subject)
        self.scan_suffix[key] = list(result.suffix_parts)
        if result.session is not None:
            self.scan_sessions[key] = result.session
        if closing:
            if result.pending is not None:
                self.scan_pending[key] = result.pending
            else:
                self.scan_pending.pop(key, None)
        if closing and refresh:
            self.set_status(result.message)
            self.refresh_view()

    def action_discard_manual_name(self) -> None:
        self._begin()
        if not self._visible or self._cursor not in self.manual:
            self.set_status("This file has no staged manual name to discard.")
            self._update_status()
            return
        index = self._cursor
        path = self.files[index]

        def discard() -> None:
            self.manual.pop(index, None)
            self.set_status(
                "Manual name discarded. Naming rules, locks and photo-review tags kept."
            )
            self.refresh_view()

        self._ask(
            f"Discard the staged manual name for {path.name}? "
            "Keeps naming rules, locks, selection and photo-review tags. No disk writes. y or n",
            discard,
        )

    def action_reset_rules(self) -> None:
        self._begin()
        reset_name_rules(self.rules)
        self._facts.clear()
        self.set_status("Naming rules reset. Manual names, locks and photo-review staging kept.")
        self.refresh_view()

    def action_lock(self) -> None:
        self._begin()
        if self.date_mode or self.mtime_mode:
            self.set_status("Date or mtime mode is on.")
            self._update_status()
            return
        if not self._visible:
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
        self._recompute_changing()
        self._paint_rows([index])

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
        self._recompute_changing()
        self._paint_rows(self.selected)

    def action_unlock_all(self) -> None:
        self._begin()
        if not self.locked:
            self.set_status("No files are locked")
            self._update_status()
            return
        count = len(self.locked)
        indexes = list(self.locked)
        self.locked.clear()
        self.set_status(f"Unlocked all {count} locked files.")
        self._recompute_changing()
        self._paint_rows(indexes)

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

        def refresh() -> None:
            self.reload_files()
            self.set_status("File list refreshed")
            self.refresh_view()

        if self.selected or self.locked or self.manual:
            self._ask(
                "Refresh clears selections, locks and staged manual names. "
                "Photo-review staging is kept for files still present. Continue? y or n",
                refresh,
            )
        else:
            refresh()

    def action_insert(self) -> None:
        self._begin()
        if self.visual or self.date_mode or self.mtime_mode:
            self.set_status("Cannot enter insert mode while in another mode.")
            self._update_status()
            return
        if not self._visible:
            return
        stem, extension = split_stem(self._ordinary_name(self._cursor))

        def done(result: tuple[str, str] | None) -> None:
            if not result:
                return
            _kind, name = result
            self._save_manual(name)
            self.refresh_view()

        self.push_screen(InsertScreen(stem, extension), done)

    def _save_manual(self, name: str) -> None:
        cleaned = name.strip()
        if cleaned:
            self.manual[self._cursor] = cleaned
            self.set_status("Name staged. Use Apply to write changes.")
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

    def _formatted_scan_parts(self, path: Path) -> list[str]:
        key = str(path)
        session = self.scan_sessions.get(key)
        if session is not None:
            return format_scan_selected(session.bits, self.rules)
        return format_scan_parts(self.scan_suffix.get(key, []), self.rules)

    def _remap_scan_map(
        self, store: dict[str, list[str]], path_map: dict[Path, Path]
    ) -> dict[str, list[str]]:
        moved: dict[str, list[str]] = {}
        for key, parts in store.items():
            dest = path_map.get(Path(key), Path(key))
            moved[str(dest)] = parts
        return moved

    def _remap_sessions(self, path_map: dict[Path, Path]) -> dict[str, ScanSession]:
        moved: dict[str, ScanSession] = {}
        for key, session in self.scan_sessions.items():
            dest = path_map.get(Path(key), Path(key))
            moved[str(dest)] = session
        return moved

    def _write_scan_notes(self, targets: set[Path]) -> list[str]:
        errors: list[str] = []
        for path in targets:
            session = self.scan_sessions.get(str(path))
            if session is None:
                continue
            payload = ScanNotes(
                front=path.name,
                back=session.related_name,
                pairing_reason=session.related_reason or "none",
                pairing_confirmed=session.related_name is not None,
                engine=session.engine or "manual",
                model=session.model if session.engine else "",
                raw_transcription=session.raw,
                notes="",
                dates=[item.date for item in session.bits if item.date is not None],
                bits=session.bits,
                related_reason=session.related_reason,
                name_suffix=list(
                    self.scan_accepted.get(
                        str(path), format_scan_selected(session.bits, self.rules)
                    )
                ),
            )
            try:
                save_notes(notes_path_for(path), payload)
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        return errors

    def _ordinary_name(self, index: int) -> str:
        path = self.files[index]
        if index in self.locked:
            return self.locked[index]
        if index in self.manual:
            return self.manual[index]
        return preview_name(
            path.name,
            self.facts_for(path),
            self.rules,
            sibling_stems=self._sibling_stems(index),
        )

    def _ordinary_stem_ext(self, index: int) -> tuple[str, str]:
        path = self.files[index]
        if index in self.locked:
            return os.path.splitext(self.locked[index])
        if index in self.manual:
            return os.path.splitext(self.manual[index])
        accepted = self.scan_accepted.get(str(path), [])
        file_stem, _ext = os.path.splitext(path.name)
        bare = strip_accepted_suffix(file_stem, accepted)
        return preview_base(
            path.name,
            self.facts_for(path),
            self.rules,
            stem=bare,
            sibling_stems=self._sibling_stems(index),
        )

    def _rename_pairs(self) -> list[tuple[Path, str]]:
        names: dict[int, str] = {
            index: self.preview_for(index) for index in self.locked if index < len(self.files)
        }
        for index, path in enumerate(self.files):
            if index in names:
                continue
            if (
                index not in self.selected
                and index not in self.manual
                and str(path) not in self.scan_suffix
            ):
                continue
            new_name = self.preview_for(index)
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
        pending = list(self.scan_pending.values())
        if not pairs and not pending:
            self.set_status("No changes to apply.")
            self._update_status()
            return
        parts = []
        if pairs:
            parts.append(f"Rename {len(pairs)} files")
        date_n = sum(1 for item in pending if item.moment is not None)
        desc_n = sum(1 for item in pending if item.description)
        skip_n = sum(
            1
            for item in pending
            if item.moment is not None and not item.overwrite and self.facts_for(item.path).has_exif
        )
        if date_n:
            extra = f", skip {skip_n} existing capture dates" if skip_n else ""
            parts.append(f"write {date_n} written calendar-day capture date(s) at noon{extra}")
        if desc_n:
            parts.append(f"write {desc_n} description(s) on JPEG/TIFF")
        question = "; ".join(parts) + "? Session undo only. y or n"
        question += self._hidden_scope([src for src, _ in pairs] + [item.path for item in pending])
        self._ask(question, lambda: self._do_apply(pairs, pending))

    def _do_rename(self, pairs: list[tuple[Path, str]]) -> None:
        self._do_apply(pairs, [])

    def _do_apply(
        self,
        pairs: list[tuple[Path, str]],
        pending: list[PendingScan],
    ) -> None:
        ops: list[RenameOp | MetaOp] = []
        path_map: dict[Path, Path] = {}
        renamed = 0
        conflicts = 0
        errors: list[str] = []
        note_targets = {src for src, _name in pairs}
        note_targets.update(item.path for item in pending)
        for path in note_targets:
            key = str(path)
            if key in self.scan_suffix:
                self.scan_accepted[key] = self._formatted_scan_parts(path)
        note_paths = [path for path in sorted(note_targets) if str(path) in self.scan_sessions]
        notes_changed = bool(note_paths)
        note_errors = self._write_scan_notes(note_targets)
        errors.extend(note_errors)
        details = [f"Notes write requested: {path.name}" for path in note_paths]
        if pairs:
            outcome = rename_paths(pairs)
            ops.extend(RenameOp(src, dst) for src, dst in outcome.done)
            extra = notes_renames(outcome.done)
            if extra:
                notes_changed = True
                sidecar_out = rename_paths(extra)
                errors.extend(sidecar_out.errors)
                details.extend(
                    f"Notes renamed: {src.name} → {dst.name}" for src, dst in sidecar_out.done
                )
                ops.extend(RenameOp(src, dst) for src, dst in sidecar_out.done)
                name_map = {src.name: dst.name for src, dst in outcome.done}
                for _src, dst in sidecar_out.done:
                    try:
                        rewrite_pairing_names(dst, name_map)
                    except OSError as exc:
                        errors.append(f"{dst.name}: updating notes references failed: {exc}")
            path_map = {src: dst for src, dst in outcome.done}
            self.scan_suffix = self._remap_scan_map(self.scan_suffix, path_map)
            self.scan_accepted = self._remap_scan_map(self.scan_accepted, path_map)
            self.scan_sessions = self._remap_sessions(path_map)
            renamed = len(outcome.done)
            conflicts = outcome.conflicts
            errors.extend(outcome.errors)
            details.extend(f"Renamed: {src.name} → {dst.name}" for src, dst in outcome.done)
            if conflicts:
                details.append(f"{conflicts} destination collisions used numbered suffixes.")
        skipped = 0
        dates = 0
        descs = 0
        for item in pending:
            path = path_map.get(item.path, item.path)
            if not path.exists():
                errors.append(f"{path.name}: missing; metadata was not written")
                continue
            facts = self.facts_for(path)
            write_date = item.moment is not None
            if write_date and not item.overwrite and facts.has_exif:
                write_date = False
                skipped += 1
                details.append(
                    f"Skipped capture date: {path.name} already has embedded capture metadata"
                )
            write_desc = bool(item.description) and path.suffix.lower() in IMAGE_EXTENSIONS
            if not write_date and not write_desc:
                continue
            try:
                op = self.journal.snapshot(path)
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if write_date and item.moment is not None:
                if not set_filesystem_times(path, item.moment, setfile=self.setfile):
                    errors.append(f"{path.name}: some filesystem times could not be written")
                if write_file_metadata_and_offset(path, item.moment, exiftool=self.exiftool):
                    dates += 1
                    details.append(f"Capture date written: {path.name}")
                else:
                    errors.append(f"{path.name}: capture metadata write failed")
            if write_desc:
                if write_image_description(path, item.description):
                    descs += 1
                    details.append(f"Description written: {path.name}")
                else:
                    errors.append(f"{path.name}: description write failed")
            ops.append(op)
        kind = "apply" if pending else "rename"
        batch = Batch(kind, ops)
        if notes_changed:
            batch.notes.append(
                "Photo-review notes contents are not restored by undo; "
                "newly created notes may remain."
            )
        self.journal.push(batch)
        self.scan_pending.clear()
        self.reload_files()
        parts: list[str] = []
        if pairs:
            parts.append(f"Renamed {renamed} files.")
            if conflicts:
                parts.append(f"{conflicts} conflicts.")
        if dates:
            parts.append(f"Wrote {dates} capture date(s).")
        if skipped:
            parts.append(f"Skipped {skipped} existing capture date(s).")
        if descs:
            parts.append(f"Wrote {descs} description(s).")
        if errors:
            parts.append(f"{len(errors)} errors.")
            self.debug("; ".join(errors))
        details.extend(f"Failed: {error}" for error in errors)
        self._record_result(" ".join(parts) if parts else "Applied notes.", details, batch)
        self.refresh_view()

    def _ask_mtime(self) -> None:
        if not self.selected:
            self.set_status("No files selected. Press space to select.")
            self._update_status()
            return
        indexes = [index for index in sorted(self.selected) if index < len(self.files)]
        self._ask(
            f"Set mtime for {len(indexes)} files?"
            + self._hidden_scope(self.files[index] for index in indexes),
            lambda: self._do_mtime(indexes),
        )

    def _do_mtime(self, indexes: list[int]) -> None:
        local = _local_zone()
        batch = Batch("mtime")
        updated = 0
        errors = 0
        details: list[str] = []
        for index in indexes:
            path = self.files[index]
            _label, moment = mtime_source(self.facts_for(path), self.rules, local)
            if moment is None:
                errors += 1
                details.append(f"Skipped: {path.name}; no eligible date source")
                continue
            try:
                op = self.journal.snapshot(path, mode="times")
            except OSError as exc:
                errors += 1
                details.append(f"Failed: {path.name}; snapshot: {exc}")
                continue
            try:
                stamp = moment.timestamp()
                os.utime(path, (stamp, stamp))
            except OSError as exc:
                errors += 1
                self.debug(f"Error setting mtime: {exc}")
                details.append(f"Failed: {path.name}; {exc}")
                continue
            batch.ops.append(op)
            updated += 1
            details.append(f"File times updated: {path.name}")
        self.journal.push(batch)
        self._facts.clear()
        self.mtime_mode = False
        message = f"Updated mtime for {updated} files."
        if errors:
            message += f" {errors} failed."
        self._record_result(message, details, batch)
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
        question += self._hidden_scope(item.path for item in todo)
        self._ask(question, lambda: self._do_dates(todo))

    def _do_dates(self, todo: list[DatePlanItem]) -> None:
        zone = self.target_zone()
        batch = Batch("dates")
        ok = 0
        errors = 0
        meta_ok = 0
        notes: list[str] = []
        details: list[str] = []
        for item in todo:
            try:
                moment = datetime.fromtimestamp(item.target, tz=zone)
            except (OSError, OverflowError, ValueError) as exc:
                errors += 1
                self.debug(f"Error setting date for {item.path.name}: {exc}")
                details.append(f"Failed: {item.path.name}; {exc}")
                continue
            try:
                op = self.journal.snapshot(item.path)
            except OSError as exc:
                errors += 1
                self.debug(f"Snapshot failed for {item.path.name}: {exc}")
                details.append(f"Failed: {item.path.name}; snapshot: {exc}")
                continue
            if not set_filesystem_times(item.path, moment, setfile=self.setfile):
                errors += 1
                batch.ops.append(op)
                details.append(
                    f"Partial write: {item.path.name}; some filesystem times failed; "
                    "recorded for undo"
                )
                continue
            if write_file_metadata_and_offset(item.path, moment, exiftool=self.exiftool):
                meta_ok += 1
                details.append(f"Dates and capture metadata written: {item.path.name}")
            else:
                details.append(
                    f"Partial write: {item.path.name}; file times updated, "
                    "capture metadata not written"
                )
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
        self._record_result(message, details, batch)
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
        question += self._hidden_scope(self.files[index] for index in indexes)
        self._ask(question, lambda: self._do_timezone(indexes))

    def _do_timezone(self, indexes: list[int]) -> None:
        zone = self.target_zone()
        batch = Batch("timezone")
        counts = WriteCounts()
        notes: list[str] = []
        details: list[str] = []
        for index in indexes:
            path = self.files[index]
            moment = datetime_for_write(path, self.facts_for(path), zone)
            if moment is None:
                counts.skipped += 1
                details.append(f"Skipped: {path.name}; no date available")
                continue
            try:
                op = self.journal.snapshot(path)
            except OSError as exc:
                counts.skipped += 1
                self.debug(f"Snapshot failed for {path.name}: {exc}")
                details.append(f"Failed: {path.name}; snapshot: {exc}")
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
            batch.ops.append(op)
            meta_state = (
                "not supported" if metadata_ok is None else "written" if metadata_ok else "failed"
            )
            details.append(
                f"{path.name}: file times {'written' if filesystem_ok else 'failed'}; "
                f"metadata {meta_state}"
            )
            if metadata_ok is False:
                self.debug(f"Metadata write failed for {path.name}")
        self.journal.push(batch)
        self._facts.clear()
        message = timezone_write_message(counts)
        if notes:
            message += " " + " ".join(notes)
        self._record_result(message, details, batch)
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
            f"Write geodata to {len(indexes)} files?"
            + self._hidden_scope(self.files[index] for index in indexes),
            lambda: self._do_geodata(indexes),
        )

    def _do_geodata(self, indexes: list[int]) -> None:
        batch = Batch("geodata")
        updated = 0
        errors = 0
        details: list[str] = []
        for index in indexes:
            path = self.files[index]
            raw = self.facts_for(path).geodata_raw
            if not raw:
                errors += 1
                details.append(f"Skipped: {path.name}; no JSON GPS coordinates")
                continue
            try:
                op = self.journal.snapshot(path)
            except OSError as exc:
                errors += 1
                self.debug(f"Snapshot failed for {path.name}: {exc}")
                details.append(f"Failed: {path.name}; snapshot: {exc}")
                continue
            if write_geodata(path, raw["latitude"], raw["longitude"]):
                updated += 1
                details.append(f"GPS written: {path.name}")
            else:
                errors += 1
                self.debug(f"Failed to write geodata to {path.name}")
                details.append(f"Failed: {path.name}; GPS write failed")
            batch.ops.append(op)
        self.journal.push(batch)
        self._facts.clear()
        message = f"Wrote geodata to {updated} files."
        if errors:
            message += f" {errors} failed."
        self._record_result(message, details, batch)
        self.refresh_view()

    def action_undo(self) -> None:
        self._begin()
        self._preview_gen += 1
        self.workers.cancel_group(self, "preview")
        self.workers.cancel_group(self, "preview-show")
        self._thumbs = file_preview.ThumbCache()
        self._facts.clear()
        self.query("#preview-image").remove()

        def restore_birth(path: Path, birth: float) -> None:
            moment = utc_from_timestamp(birth)
            if moment is None:
                return
            set_birthtime(path, moment.astimezone(), setfile=self.setfile)

        last = self.journal.batches[-1] if self.journal.batches else None
        reverse = (
            {op.dst: op.src for op in last.ops if isinstance(op, RenameOp)}
            if last is not None
            else {}
        )
        result = self.journal.undo(restore_birth=restore_birth)
        if result is None:
            self.set_status("Nothing to undo.")
            self._update_status()
            return
        if reverse:
            self.scan_suffix = self._remap_scan_map(self.scan_suffix, reverse)
            self.scan_accepted = self._remap_scan_map(self.scan_accepted, reverse)
            self.scan_sessions = self._remap_sessions(reverse)
        self.reload_files()
        message = (
            f"Undid {BATCH_LABEL.get(result.kind, result.kind)} ({result.restored} steps restored)."
        )
        if result.notes:
            message += " " + " ".join(result.notes)
        details = list(result.notes)
        if last is not None:
            details.extend(
                f"Restore attempted: {op.dst.name} → {op.src.name}"
                if isinstance(op, RenameOp)
                else f"Restore attempted: {op.path.name} (metadata / file times)"
                for op in reversed(last.ops)
            )
        self._record_result(message, details)
        self.refresh_view()


def _local_zone() -> tzinfo:
    zone = datetime.now().astimezone().tzinfo
    assert zone is not None
    return zone


def _format_stamp(stamp: float, zone: tzinfo | None) -> str:
    try:
        return datetime.fromtimestamp(stamp, tz=zone).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "Error"


def _format_date_diff(
    cur_stamp: float | None,
    target_stamp: float,
    zone: tzinfo | None,
    *,
    changing: bool = False,
) -> Text:
    """Return a Rich :class:`~rich.text.Text` object representing the date
    difference between *cur_stamp* and *target_stamp*.

    When *changing* is ``True`` the whole string is styled as ``"dark_orange"``
    to match the normal mode's "will change" styling.  The per‑segment
    ``yellow``/``dim`` logic from the original implementation is omitted because
    the cell already conveys the change state via the outer style.
    """

    target_str = _format_stamp(target_stamp, zone)
    if cur_stamp is None or target_str == "Error":
        # No current stamp or formatting error – just show the target as plain
        # text.  ``changing`` is irrelevant because the cell will not be
        # highlighted in this scenario.
        return Text(target_str)

    cur_str = _format_stamp(cur_stamp, zone)
    if cur_str == "Error" or cur_str == target_str:
        # Identical or error – dim the output so it mirrors the existing
        # behaviour for unchanged dates.
        return Text(target_str, style="dim")

    # ``changing`` indicates that the file is scheduled to be renamed.  In that
    # case we override any per‑segment styling and paint the whole cell in a
    # single orange tone.
    if changing:
        return Text(target_str, style="dark_orange")

    # If we get here ``changing`` is ``False`` and we want the original
    # segment‑based highlighting.
    try:
        cur_dt = datetime.fromtimestamp(cur_stamp, tz=zone)
        target_dt = datetime.fromtimestamp(target_stamp, tz=zone)
        date_changed = cur_dt.date() != target_dt.date()
        time_changed = cur_dt.time() != target_dt.time()
    except (OSError, OverflowError, ValueError):
        return Text(target_str)

    date_style = "yellow" if date_changed else "dim"
    time_style = "yellow" if time_changed else "dim"

    res = Text()
    res.append(target_str[:10], style=date_style)
    res.append(" ")
    res.append(target_str[11:], style=time_style)
    return res


def _format_diff_summary(
    cur_stamp: float | None, target_stamp: float, prec: str, nudged: bool
) -> str:
    parts: list[str] = []
    if prec == "kept":
        parts.append("date-only; kept existing clock")
    elif prec == "date":
        parts.append("date-only; set to 12:00")
    elif prec in ("second", "minute"):
        parts.append("exact timestamp from filename")
    if nudged:
        parts.append("nudged +1s (collision)")

    if cur_stamp is not None:
        delta = target_stamp - cur_stamp
        if abs(delta) < 1:
            diff_str = "no change"
        else:
            sign = "+" if delta > 0 else "-"
            secs = abs(int(delta))
            days, rem = divmod(secs, 86400)
            hrs, rem = divmod(rem, 3600)
            mins, _ = divmod(rem, 60)
            d_parts: list[str] = []
            if days:
                d_parts.append(f"{days}d")
            if hrs:
                d_parts.append(f"{hrs}h")
            if mins and not days:
                d_parts.append(f"{mins}m")
            if not d_parts:
                d_parts.append(f"{secs}s")
            diff_str = sign + " ".join(d_parts)
    else:
        diff_str = "unknown"
    return diff_str + " (" + ", ".join(parts) + ")"
