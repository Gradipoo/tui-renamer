"""Review the proposed timestamp changes before approving a batch."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, OptionList, Select, Static

ReconcileResult = tuple[list[int | None], bool, bool, bool]
DescribeReconcile = Callable[[list[int | None], bool, bool, bool], tuple[str, list[str], bool]]
SKIP = -1


class ReconcileScreen(ModalScreen[ReconcileResult | None]):
    """Inspect rows and choose which destinations may receive a timestamp."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]
    DEFAULT_CSS = """
    ReconcileScreen { align: center middle; }
    ReconcileScreen #reconcile-dialog {
        width: 96%; max-width: 124; height: 96%;
        border: solid $primary; background: $surface; padding: 0 1;
    }
    ReconcileScreen #reconcile-body { height: 1fr; }
    ReconcileScreen #reconcile-rows { width: 34%; height: 1fr; }
    ReconcileScreen #reconcile-inspect { width: 1fr; height: 1fr; padding-left: 1; }
    ReconcileScreen #reconcile-detail-scroll { height: 1fr; }
    ReconcileScreen Horizontal, ReconcileScreen Select { height: auto; }
    ReconcileScreen Checkbox { height: auto; }
    """

    def __init__(
        self,
        paths: list[Path],
        choices: list[list[str]],
        describe: DescribeReconcile,
        *,
        initial: list[int | None] | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.choices = choices
        self.selected = (
            initial.copy()
            if initial is not None
            else [0 if options else None for options in choices]
        )
        self.describe = describe
        self.details: list[str] = []
        self.row: int | None = None
        self.summary = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="reconcile-dialog"):
            yield Static("Reconcile timestamps", markup=False)
            with Horizontal(id="reconcile-body"):
                yield OptionList(
                    *(self._row_label(index) for index in range(len(self.paths))),
                    id="reconcile-rows",
                    markup=False,
                )
                with Vertical(id="reconcile-inspect"):
                    yield Select(
                        [("Skip this file", SKIP)],
                        allow_blank=False,
                        compact=True,
                        id="reconcile-source",
                        disabled=True,
                    )
                    with VerticalScroll(id="reconcile-detail-scroll"):
                        yield Static("", id="reconcile-details", markup=False)
            yield Checkbox("Rename files", True, id="reconcile-filenames", compact=True)
            yield Checkbox("Set filesystem times", True, id="reconcile-filesystem", compact=True)
            yield Checkbox("Write capture metadata", False, id="reconcile-capture", compact=True)
            yield Static("", id="reconcile-summary", markup=False)
            with Horizontal():
                yield Button("Apply", variant="primary", id="reconcile-apply", disabled=True)
                yield Button("Cancel", id="reconcile-cancel")

    def on_mount(self) -> None:
        self._refresh_plan()
        if self.paths:
            self.query_one("#reconcile-rows", OptionList).highlighted = 0
            self._show_row(0)
        self.query_one("#reconcile-cancel", Button).focus()

    def _row_label(self, index: int) -> str:
        name = self.paths[index].name
        return f"Skip · {name}" if self.selected[index] is None else name

    def _result(self) -> ReconcileResult:
        return (
            self.selected.copy(),
            self.query_one("#reconcile-filenames", Checkbox).value,
            self.query_one("#reconcile-filesystem", Checkbox).value,
            self.query_one("#reconcile-capture", Checkbox).value,
        )

    def _refresh_plan(self) -> None:
        self.summary, self.details, can_apply = self.describe(*self._result())
        self.query_one("#reconcile-summary", Static).update(self.summary)
        self.query_one("#reconcile-apply", Button).disabled = not can_apply
        if self.row is not None:
            self.query_one("#reconcile-details", Static).update(self.details[self.row])

    def _show_row(self, index: int) -> None:
        if index == self.row:
            return
        self.row = index
        source = self.query_one("#reconcile-source", Select)
        source.disabled = not self.choices[index]
        with source.prevent(Select.Changed):
            source.set_options(
                [
                    *((label, value) for value, label in enumerate(self.choices[index])),
                    ("Skip this file", SKIP),
                ]
            )
            selected = self.selected[index]
            source.value = SKIP if selected is None else selected
        self.query_one("#reconcile-details", Static).update(self.details[index])
        self.query_one("#reconcile-detail-scroll", VerticalScroll).scroll_home(animate=False)

    @on(OptionList.OptionHighlighted, "#reconcile-rows")
    def show_highlighted_row(self, event: OptionList.OptionHighlighted) -> None:
        self._show_row(event.option_index)

    @on(OptionList.OptionSelected, "#reconcile-rows")
    def focus_source(self, event: OptionList.OptionSelected) -> None:
        self._show_row(event.option_index)
        self.query_one("#reconcile-source", Select).focus()

    @on(Select.Changed, "#reconcile-source")
    def choose_source(self, event: Select.Changed) -> None:
        if (
            self.row is None
            or event.value != event.select.value
            or not isinstance(event.value, int)
        ):
            return
        selected = None if event.value == SKIP else event.value
        if self.selected[self.row] != selected:
            self.selected[self.row] = selected
            self.query_one("#reconcile-rows", OptionList).replace_option_prompt_at_index(
                self.row, self._row_label(self.row)
            )
            self._refresh_plan()

    @on(Checkbox.Changed)
    def change_destinations(self) -> None:
        self._refresh_plan()

    @on(Button.Pressed)
    def answer(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "reconcile-cancel":
            self.dismiss(None)
        elif event.button.id == "reconcile-apply":
            previous = self.summary, self.details
            self._refresh_plan()
            if previous != (self.summary, self.details):
                self.query_one("#reconcile-summary", Static).update(
                    self.summary + "\nThe plan changed. Review it before applying."
                )
            elif not event.button.disabled:
                self.dismiss(self._result())
