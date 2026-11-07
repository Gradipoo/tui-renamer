"""Photo review overlay. Analysis is optional and cancellable."""

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
from tui_renamer.scan_bits import (
    Bit,
    PendingScan,
    StageResult,
    bits_from_recognition,
    capture_from_bits,
    description_from_bits,
    staged_filename,
)
from tui_renamer.scan_candidates import score_candidates
from tui_renamer.scan_config import ScanSettings, host_is_local, save_scan_settings
from tui_renamer.scan_dates import parse_date_text
from tui_renamer.scan_models import (
    pull_command,
    setup_lines,
    suggested_rec,
    total_ram_bytes,
)
from tui_renamer.scan_notes import ScanNotes, notes_path_for, save_notes


class ReviewScreen(ModalScreen[StageResult | None]):
    """Analyze the current photo. A related file is optional."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("r", "read", show=False),
        Binding("x", "cancel_read", show=False),
        Binding("a", "accept", show=False),
        Binding("b", "related", show=False),
        Binding("i", "installed", show=False),
        Binding("question_mark", "help", show=False),
        Binding("o", "toggle_overwrite", show=False),
    ]

    def __init__(
        self,
        files: list[Path],
        *,
        start: Path,
        backend: ScanBackend,
        settings: ScanSettings,
        zone=None,
        has_capture: bool = False,
    ) -> None:
        super().__init__()
        self.files = files
        self.subject = start
        self.related: Path | None = None
        self.related_reason = ""
        self.backend = backend
        self.settings = settings
        self.zone = zone
        self.has_capture = has_capture
        self.candidates = score_candidates(start, files)
        self.cache = ThumbCache()
        self._busy = False
        self._job = 0
        self._pick_kind = ""
        self._ram = total_ram_bytes()
        self._installed: list[str] | None = None
        self._loaded: list[str] = []
        self._discover_error = ""
        self._remote_ok = False
        self._local = host_is_local(settings.host)
        self._overwrite = False
        self._pick_models: list[str] = []
        self.bits: list[Bit] = []
        self._chip_gen = 0
        self._engine = ""
        self._raw = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="scan-dialog"):
            yield Static("Photo review", markup=False)
            yield Static("", id="scan-photo", markup=False)
            yield Vertical(id="scan-preview-slot")
            yield Input(placeholder="vision model, Enter runs", id="scan-model")
            yield Static("", id="scan-models", markup=False)
            yield Static("", id="scan-status", markup=False)
            yield Static("", id="scan-source", markup=False)
            yield Horizontal(id="scan-chips")
            yield Input(placeholder="add a name or date chip", id="scan-add")
            with Horizontal(id="scan-actions"):
                yield Button(
                    "r Read",
                    id="scan-read",
                    compact=True,
                    tooltip="Read the chosen image",
                )
                yield Button(
                    "x Cancel",
                    id="scan-cancel",
                    compact=True,
                    tooltip="Cancel the reading",
                )
                yield Button(
                    "a Accept",
                    id="scan-accept",
                    compact=True,
                    tooltip="Save notes and stage",
                )
                yield Button(
                    "b Related",
                    id="scan-related",
                    compact=True,
                    tooltip="Pick a related file",
                )
                yield Button(
                    "i Installed",
                    id="scan-installed",
                    compact=True,
                    tooltip="Pick a local vision model",
                )
                yield Button(
                    "o Skip existing date",
                    id="scan-overwrite",
                    compact=True,
                    tooltip="Skip existing capture dates unless this says overwrite",
                )
                yield Button(
                    "? Help",
                    id="scan-help-btn",
                    compact=True,
                    tooltip="Setup help",
                )
                yield Button(
                    "Esc Close",
                    id="scan-close",
                    compact=True,
                    tooltip="Close without staging",
                )
            yield OptionList(id="scan-pick")
            yield Static("", id="scan-help", markup=False)
            yield Static("", id="scan-cmd", markup=False)

    def on_mount(self) -> None:
        self.query_one("#scan-model", Input).value = self.settings.model
        self.query_one("#scan-pick", OptionList).display = False
        self.query_one("#scan-help", Static).display = False
        self._paint_header()
        self._fill_preview()
        self.query_one("#scan-model", Input).focus()
        self._discover()

    def action_close(self) -> None:
        self._cancel_worker()
        self.dismiss(None)

    def action_help(self) -> None:
        box = self.query_one("#scan-help", Static)
        if box.display:
            box.display = False
            return
        lines = setup_lines(
            host=self.settings.host,
            local_host=self._local,
            total_bytes=self._ram,
            installed=self._installed,
            discover_error=self._discover_error,
        )
        box.update("\n".join(lines))
        box.display = True

    def action_toggle_overwrite(self) -> None:
        self._overwrite = not self._overwrite
        label = "o Overwrite existing date" if self._overwrite else "o Skip existing date"
        self.query_one("#scan-overwrite", Button).label = label

    def action_installed(self) -> None:
        if not self._local:
            self._status("Installed list is only for a local Ollama host.")
            return
        names = self._installed
        if names is None:
            self._status("Still checking local Ollama.")
            return
        if not names:
            rec = suggested_rec(self._ram)
            self._status(f"No vision models. Pull one with: {rec.pull_command()}")
            self.query_one("#scan-cmd", Static).update(rec.pull_command())
            return
        picker = self.query_one("#scan-pick", OptionList)
        picker.clear_options()
        loaded = set(self._loaded)
        self._pick_models = names
        for index, name in enumerate(names):
            mark = " (loaded)" if name in loaded else ""
            picker.add_option(Option(f"{name}{mark}", id=f"mdl-{index}"))
        picker.display = True
        self._pick_kind = "model"
        picker.focus()

    def action_related(self) -> None:
        if not self.candidates:
            self._status("No other images in this folder.")
            return
        picker = self.query_one("#scan-pick", OptionList)
        picker.clear_options()
        picker.add_option(Option("This photo only", id="__none__"))
        for item in self.candidates:
            picker.add_option(Option(item.line(), id=str(item.path)))
        picker.display = True
        self._pick_kind = "related"
        picker.focus()

    def action_read(self) -> None:
        if self._busy:
            return
        model = self.query_one("#scan-model", Input).value.strip()
        if not model:
            self._status("No model. Type notes as chips, or pick a vision model.")
            return
        self._persist_model(model)
        if not self._local and not self._remote_ok:
            self._remote_ok = True
            self._status(
                f"This host is not this computer. Read again to send the image to "
                f"{self.settings.host}."
            )
            return
        source = self.related or self.subject
        self._busy = True
        self._job += 1
        self._status("Reading.")
        self._run_read(source, model, self._job)

    def action_cancel_read(self) -> None:
        self._cancel_worker()
        self._busy = False
        self._job += 1
        self._status("Reading cancelled.")

    def action_accept(self) -> None:
        if self._busy:
            self._status("Wait for the reading, or cancel it.")
            return
        model = self.query_one("#scan-model", Input).value.strip()
        self._persist_model(model)
        payload = ScanNotes(
            front=self.subject.name,
            back=self.related.name if self.related else None,
            pairing_reason=self.related_reason or "none",
            pairing_confirmed=self.related is not None,
            engine=self._engine or "manual",
            model=model if self._engine else "",
            raw_transcription=self._raw,
            notes="",
            dates=[item.date for item in self.bits if item.date is not None],
            bits=self.bits,
            related_reason=self.related_reason,
        )
        try:
            save_notes(notes_path_for(self.subject), payload)
        except OSError:
            self._status("The notes sidecar could not be written.")
            return
        new_name = staged_filename(self.subject, self.bits)
        pending = None
        moment = None
        date_text = ""
        if self.zone is not None:
            moment, date_text = capture_from_bits(self.bits, self.zone)
        description = description_from_bits(self.bits)
        if moment is not None or description:
            pending = PendingScan(
                path=self.subject,
                moment=moment,
                noon=moment is not None,
                description=description,
                date_text=date_text,
                overwrite=self._overwrite,
            )
        extra = ""
        suffix = self.subject.suffix.lower()
        if description and suffix not in {".jpg", ".jpeg", ".tif", ".tiff"}:
            extra = " Description stays in the sidecar for this type."
        self.dismiss(
            StageResult(
                message=(f"Staged notes for {self.subject.name}. Apply writes the disk.{extra}"),
                subject=self.subject,
                new_name=new_name,
                pending=pending,
            )
        )

    @on(Input.Submitted, "#scan-model")
    def _model_enter(self) -> None:
        self.action_read()

    @on(Input.Submitted, "#scan-add")
    def _add_enter(self) -> None:
        self._add_chip()

    @on(OptionList.OptionSelected, "#scan-pick")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        ident = event.option_id
        kind = self._pick_kind
        self._pick_kind = ""
        self.query_one("#scan-pick", OptionList).display = False
        if kind == "model" and ident:
            name = ident
            if ident.startswith("mdl-"):
                index = int(ident.split("-", 1)[1])
                name = self._pick_models[index]
            self.query_one("#scan-model", Input).value = name
            self._persist_model(name)
            self._status(f"Model set to {name}.")
            return
        if kind == "related":
            if ident == "__none__" or not ident:
                self.related = None
                self.related_reason = ""
            else:
                path = Path(ident)
                match = next((item for item in self.candidates if item.path == path), None)
                self.related = path
                self.related_reason = match.reason if match else "manual"
            self._paint_header()
            self._fill_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id or ""
        if ident.startswith("scan-bit-"):
            index = int(ident.rsplit("-", 1)[-1])
            if 0 <= index < len(self.bits):
                self.bits[index].cycle()
                self._paint_chips()
            return
        run = {
            "scan-read": self.action_read,
            "scan-cancel": self.action_cancel_read,
            "scan-accept": self.action_accept,
            "scan-related": self.action_related,
            "scan-installed": self.action_installed,
            "scan-overwrite": self.action_toggle_overwrite,
            "scan-help-btn": self.action_help,
            "scan-close": self.action_close,
        }.get(ident)
        if run:
            run()

    def _add_chip(self) -> None:
        box = self.query_one("#scan-add", Input)
        text = box.value.strip()
        if not text:
            return
        box.value = ""
        dates = parse_date_text(text)
        if dates:
            for date in dates:
                self.bits.append(
                    Bit(
                        kind="date",
                        label=date.text,
                        source=self.subject.name,
                        model="",
                        origin="written",
                        date=date,
                        manual=True,
                    )
                )
        else:
            self.bits.append(
                Bit(
                    kind="text",
                    label=text,
                    source=self.subject.name,
                    model="",
                    origin="written",
                    manual=True,
                )
            )
        self._paint_chips()

    def _paint_header(self) -> None:
        related = self.related.name if self.related else "none"
        self.query_one("#scan-photo", Static).update(f"{self.subject.name}  related {related}")
        source = self.related or self.subject
        self.query_one("#scan-source", Static).update(f"Read source: {source.name}")

    def _paint_chips(self) -> None:
        row = self.query_one("#scan-chips")
        row.remove_children()
        self._chip_gen += 1
        for index, item in enumerate(self.bits):
            button = Button(
                item.caption(),
                id=f"scan-bit-{self._chip_gen}-{index}",
                compact=True,
                tooltip=item.tooltip(),
            )
            button.add_class("scan-chip")
            if item.use_name or item.use_meta:
                button.add_class("-on")
            row.mount(button)

    def _paint_models(self) -> None:
        names = self._installed
        if names is None and self._local:
            line = "Checking local Ollama."
        elif self._discover_error:
            line = self._discover_error
        elif names:
            loaded = set(self._loaded)
            shown = []
            for name in names[:6]:
                shown.append(f"{name}*" if name in loaded else name)
            line = "Installed: " + ", ".join(shown)
        elif self._local:
            rec = suggested_rec(self._ram)
            line = f"No vision models. {rec.pull_command()}"
        else:
            line = f"Remote host {self.settings.host}."
        self.query_one("#scan-models", Static).update(line)
        rec = suggested_rec(self._ram)
        current = self.query_one("#scan-model", Input).value.strip()
        self.query_one("#scan-cmd", Static).update(pull_command(current or rec.tag))

    def _fill_preview(self) -> None:
        path = self.related or self.subject
        slot = self.query_one("#scan-preview-slot")
        slot.remove_children()
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
            if worker.group in {"scan-ocr", "scan-discover"}:
                if worker.group == "scan-ocr":
                    worker.cancel()

    @work(thread=True, exclusive=True, group="scan-discover", exit_on_error=False)
    def _discover(self) -> None:
        if not self._local:
            self.app.call_from_thread(self._discover_done, [], [], "")
            return
        try:
            names = self.backend.list_vision_models()
        except BackendError as exc:
            self.app.call_from_thread(self._discover_done, [], [], str(exc))
            return
        loaded: list[str] = []
        try:
            loaded = self.backend.list_loaded_models()
        except BackendError:
            loaded = []
        self.app.call_from_thread(self._discover_done, names, loaded, "")

    def _discover_done(self, names: list[str], loaded: list[str], error: str) -> None:
        self._installed = names
        self._loaded = loaded
        self._discover_error = error
        box = self.query_one("#scan-model", Input)
        if not box.value.strip():
            pick = next((item for item in loaded if item in names), None)
            if pick is None and names:
                pick = names[0]
            if pick:
                box.value = pick
        self._paint_models()

    @work(thread=True, exclusive=True, group="scan-ocr", exit_on_error=False)
    def _run_read(self, image: Path, model: str, job: int) -> None:
        try:
            result = self.backend.recognize(image, model=model)
        except BackendError as exc:
            self.app.call_from_thread(self._read_done, job, None, str(exc), image.name)
            return
        self.app.call_from_thread(self._read_done, job, result, "", image.name)

    def _read_done(
        self,
        job: int,
        result: Recognition | None,
        error: str,
        source: str,
    ) -> None:
        if job != self._job:
            return
        self._busy = False
        if error or result is None:
            self._status(error or "Reading failed.")
            return
        self._engine = result.engine
        self._raw = result.raw
        self.bits = bits_from_recognition(
            transcription=result.transcription,
            dates=result.dates,
            tokens=result.tokens,
            people_present=result.people_present,
            people_count=result.people_count,
            source=source,
            model=result.model,
        )
        self._paint_chips()
        self._status("Select chips for the name or metadata, then Accept.")
