"""Scan review overlay. Recognition is optional and cancellable."""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from tui_renamer.preview import Preview, ThumbCache, build_preview, load_image_widget
from tui_renamer.scan_backend import BackendError, Recognition, ScanBackend
from tui_renamer.scan_config import ScanSettings, save_scan_settings
from tui_renamer.scan_dates import ScanDate, parse_date_text
from tui_renamer.scan_notes import ScanNotes, load_notes, notes_path_for, save_notes
from tui_renamer.scan_pairing import PairRecord, suggest_pairs


class ReviewScreen(ModalScreen[str | None]):
    """Front/back pair, transcription, and accept. Escape closes without saving."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("j,down", "next_pair", show=False),
        Binding("k,up", "prev_pair", show=False),
        Binding("enter", "accept", show=False),
        Binding("p", "pair", show=False),
        Binding("u", "unpair", show=False),
        Binding("r", "read", show=False),
        Binding("x", "cancel_read", show=False),
    ]

    def __init__(
        self,
        files: list[Path],
        *,
        start: Path,
        backend: ScanBackend,
        settings: ScanSettings,
        selected: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self.files = files
        self.backend = backend
        self.settings = settings
        self.records = suggest_pairs(files)
        if selected and len(selected) >= 2:
            self._apply_manual(selected[0], selected[1])
        self.index = self._index_for(start)
        self.cache = ThumbCache()
        self._busy = False
        self._job = 0
        self._engine = ""
        self._raw = ""
        self._picking = False

    def compose(self) -> ComposeResult:
        with Vertical(id="scan-dialog"):
            yield Static("Scan review", markup=False)
            yield Static("", id="scan-pair", markup=False)
            with Horizontal(id="scan-previews"):
                yield Vertical(id="scan-front-slot")
                yield Vertical(id="scan-back-slot")
            yield Static(
                "Model (local Ollama). Leave blank to type the back yourself.",
                markup=False,
            )
            yield Input(placeholder="model name", id="scan-model")
            yield Static("Transcription", markup=False)
            yield Input(placeholder="text from the back", id="scan-text")
            yield Static("Notes", markup=False)
            yield Input(placeholder="optional notes", id="scan-notes")
            yield Static("Dates as written. Year only stays a year.", markup=False)
            yield Input(placeholder="1952 or 1952-06 or 1952 or 1953", id="scan-dates")
            yield Static("", id="scan-dates-view", markup=False)
            yield Static("", id="scan-status", markup=False)
            with Horizontal(id="scan-actions"):
                yield Button(
                    "r Read",
                    id="scan-read",
                    compact=True,
                    tooltip="Read the back with Ollama",
                )
                yield Button(
                    "x Cancel",
                    id="scan-cancel",
                    compact=True,
                    tooltip="Cancel the reading",
                )
                yield Button(
                    "Enter Accept",
                    id="scan-accept",
                    compact=True,
                    tooltip="Save the notes sidecar",
                )
                yield Button(
                    "u Unpair",
                    id="scan-unpair",
                    compact=True,
                    tooltip="Clear the back",
                )
                yield Button(
                    "p Pair",
                    id="scan-pair-btn",
                    compact=True,
                    tooltip="Choose a back",
                )
                yield Button(
                    "Esc Close",
                    id="scan-close",
                    compact=True,
                    tooltip="Close without saving",
                )
            yield OptionList(id="scan-pick")
            yield Static(
                "j/k next pair. Reading never writes EXIF. Accept writes a .rn-notes.json sidecar.",
                markup=False,
            )

    def on_mount(self) -> None:
        self.query_one("#scan-model", Input).value = self.settings.model
        self.query_one("#scan-pick", OptionList).display = False
        self._show()
        self.query_one("#scan-text", Input).focus()

    def action_close(self) -> None:
        self._cancel_worker()
        self.dismiss(None)

    def action_next_pair(self) -> None:
        if self._picking or not self.records:
            return
        self.index = min(self.index + 1, len(self.records) - 1)
        self._show()

    def action_prev_pair(self) -> None:
        if self._picking or not self.records:
            return
        self.index = max(self.index - 1, 0)
        self._show()

    def action_unpair(self) -> None:
        if not self.records:
            return
        record = self.records[self.index]
        record.back = None
        record.confirmed = True
        record.reason = "manual"
        record.ambiguous = False
        record.others = []
        self._status("Unpaired.")
        self._show()

    def action_pair(self) -> None:
        if not self.records:
            return
        others = [path for path in self.files if path != self.records[self.index].front]
        picker = self.query_one("#scan-pick", OptionList)
        picker.clear_options()
        if not others:
            self._status("No other file to pair.")
            return
        for path in others:
            picker.add_option(Option(path.name, id=str(path)))
        picker.display = True
        self._picking = True
        picker.focus()

    def action_read(self) -> None:
        if self._busy:
            return
        record = self.current()
        if record is None or record.back is None:
            self._status("Pair a back first, or type the text by hand.")
            return
        model = self.query_one("#scan-model", Input).value.strip()
        self._persist_model(model)
        self._busy = True
        self._job += 1
        self._status("Reading the back.")
        self._run_read(record.back, model, self._job)

    def action_cancel_read(self) -> None:
        self._cancel_worker()
        self._busy = False
        self._job += 1
        self._status("Reading cancelled.")

    def action_accept(self) -> None:
        if self._busy:
            self._status("Wait for the reading, or cancel it.")
            return
        record = self.current()
        if record is None:
            self.dismiss(None)
            return
        if record.ambiguous and not record.confirmed:
            self._status("This pair is ambiguous. Pair or unpair first.")
            return
        text = self.query_one("#scan-text", Input).value
        notes = self.query_one("#scan-notes", Input).value
        dates_blob = self.query_one("#scan-dates", Input).value
        dates = parse_date_text(dates_blob)
        model = self.query_one("#scan-model", Input).value.strip()
        self._persist_model(model)
        payload = ScanNotes(
            front=record.front.name,
            back=record.back.name if record.back else None,
            pairing_reason=record.reason,
            pairing_confirmed=True,
            engine=self._engine or "manual",
            model=model if self._engine else "",
            raw_transcription=self._raw or text,
            notes=notes,
            dates=dates,
            size_similar=record.size_similar,
        )
        try:
            save_notes(notes_path_for(record.front), payload)
        except OSError:
            self._status("The notes sidecar could not be written.")
            return
        record.confirmed = True
        self.dismiss(f"Saved notes for {record.front.name}.")

    @on(OptionList.OptionSelected, "#scan-pick")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        ident = event.option_id
        self._picking = False
        self.query_one("#scan-pick", OptionList).display = False
        if not ident or not self.records:
            return
        back = Path(ident)
        record = self.records[self.index]
        record.back = back
        record.reason = "manual"
        record.confirmed = True
        record.ambiguous = False
        record.size_similar = False
        self._status(f"Paired with {back.name}.")
        self._show()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "scan-dates":
            self._paint_dates(parse_date_text(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        run = {
            "scan-read": self.action_read,
            "scan-cancel": self.action_cancel_read,
            "scan-accept": self.action_accept,
            "scan-unpair": self.action_unpair,
            "scan-pair-btn": self.action_pair,
            "scan-close": self.action_close,
        }.get(event.button.id or "")
        if run:
            run()

    def current(self) -> PairRecord | None:
        if not self.records:
            return None
        return self.records[self.index]

    def _apply_manual(self, front: Path, back: Path) -> None:
        self.records = [
            record
            for record in self.records
            if record.front not in {front, back} and record.back not in {front, back}
        ]
        self.records.insert(
            0,
            PairRecord(front=front, back=back, reason="manual", confirmed=True),
        )

    def _index_for(self, start: Path) -> int:
        for index, record in enumerate(self.records):
            if record.front == start or record.back == start:
                return index
        return 0

    def _show(self) -> None:
        record = self.current()
        pair = self.query_one("#scan-pair", Static)
        if record is None:
            pair.update("No image files in this folder.")
            return
        back = record.back.name if record.back else "none"
        flags = [record.reason]
        if record.size_similar:
            flags.append("similar size (not confirmation)")
        if record.ambiguous:
            flags.append("ambiguous")
        if record.confirmed:
            flags.append("confirmed")
        pair.update(
            f"{self.index + 1}/{len(self.records)}  front {record.front.name}  "
            f"back {back}  ({', '.join(flags)})"
        )
        existing = load_notes(notes_path_for(record.front))
        text_box = self.query_one("#scan-text", Input)
        notes_box = self.query_one("#scan-notes", Input)
        dates_box = self.query_one("#scan-dates", Input)
        if existing is not None:
            text_box.value = existing.raw_transcription
            notes_box.value = existing.notes
            dates_box.value = "; ".join(item.text for item in existing.dates)
            self._engine = existing.engine
            self._raw = existing.raw_transcription
        else:
            text_box.value = ""
            notes_box.value = ""
            dates_box.value = ""
            self._engine = ""
            self._raw = ""
        self._paint_dates(parse_date_text(dates_box.value))
        self._fill_preview("scan-front-slot", record.front, "Front")
        self._fill_preview("scan-back-slot", record.back, "Back")

    def _paint_dates(self, dates: list[ScanDate]) -> None:
        if not dates:
            line = "No date yet. A year stays a year."
        else:
            parts = []
            for item in dates:
                extra = " uncertain" if item.uncertain else ""
                parts.append(f"{item.kind} {item.text}{extra}")
            line = "Readings: " + "; ".join(parts)
        self.query_one("#scan-dates-view", Static).update(line)

    def _fill_preview(self, slot_id: str, path: Path | None, title: str) -> None:
        slot = self.query_one(f"#{slot_id}")
        slot.remove_children()
        slot.mount(Static(title, markup=False))
        if path is None:
            slot.mount(Static("(none)", markup=False))
            return
        result: Preview = build_preview(path, self.cache)
        slot.mount(Static(result.caption, markup=False))
        widget_cls = load_image_widget()
        if result.show_image and result.image is not None and widget_cls is not None:
            try:
                slot.mount(widget_cls(result.image))
            except Exception:
                slot.mount(Static(result.body or result.caption, markup=False))
        elif result.body:
            slot.mount(Static(result.body, markup=False))

    def _status(self, message: str) -> None:
        self.query_one("#scan-status", Static).update(message)

    def _persist_model(self, model: str) -> None:
        save_scan_settings(ScanSettings(host=self.settings.host, model=model))
        self.settings = ScanSettings(host=self.settings.host, model=model)

    def _cancel_worker(self) -> None:
        for worker in self.workers:
            if worker.group == "scan-ocr":
                worker.cancel()

    @work(thread=True, exclusive=True, group="scan-ocr", exit_on_error=False)
    def _run_read(self, image: Path, model: str, job: int) -> None:
        try:
            result = self.backend.recognize(image, model=model)
        except BackendError as exc:
            self.app.call_from_thread(self._read_done, job, None, str(exc))
            return
        self.app.call_from_thread(self._read_done, job, result, "")

    def _read_done(self, job: int, result: Recognition | None, error: str) -> None:
        if job != self._job:
            return
        self._busy = False
        if error or result is None:
            self._status(error or "Reading failed.")
            return
        self._engine = result.engine
        self._raw = result.raw
        self.query_one("#scan-text", Input).value = result.transcription
        if result.notes:
            self.query_one("#scan-notes", Input).value = result.notes
        if result.dates:
            self.query_one("#scan-dates", Input).value = "; ".join(
                item.text for item in result.dates
            )
            self._paint_dates(result.dates)
        self._status("Reading ready. Edit if needed, then Accept.")
