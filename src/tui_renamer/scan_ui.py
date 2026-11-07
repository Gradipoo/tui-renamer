"""Photo review overlay. Analysis is optional and cancellable."""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
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
from tui_renamer.scan_candidates import Candidate, score_candidates
from tui_renamer.scan_config import ScanSettings, host_is_local, save_scan_settings
from tui_renamer.scan_dates import parse_date_text
from tui_renamer.scan_models import pull_command, setup_lines, suggested_rec, total_ram_bytes
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
        self.candidates: list[Candidate] = []
        self.cache = ThumbCache()
        self._busy = False
        self._job = 0
        self._discover_job = 0
        self._score_job = 0
        self._auto_started = False
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
            yield Static("", id="scan-status", markup=False)
            yield Static("", id="scan-models", markup=False)
            with Horizontal(id="scan-body"):
                with VerticalScroll(id="scan-left"):
                    yield Static("", id="scan-photo", markup=False)
                    yield Vertical(id="scan-preview-slot")
                    yield Static("", id="scan-source", markup=False)
                    yield Horizontal(id="scan-chips")
                    yield Input(placeholder="add a name or date chip", id="scan-add")
                    yield OptionList(id="scan-model-pick")
                    yield Input(placeholder="model override, Enter reads", id="scan-model")
                with VerticalScroll(id="scan-right"):
                    yield Static("Related (pick, then r to read)", markup=False)
                    yield OptionList(id="scan-related-list")
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
                    tooltip="Focus related suggestions",
                )
                yield Button(
                    "i Models",
                    id="scan-installed",
                    compact=True,
                    tooltip="Show installed vision models",
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
            yield Static("", id="scan-help", markup=False)
            yield Static("", id="scan-cmd", markup=False)

    def on_mount(self) -> None:
        self.query_one("#scan-model", Input).value = self.settings.model
        self.query_one("#scan-model-pick", OptionList).display = False
        self.query_one("#scan-help", Static).display = False
        self.query_one("#scan-cmd", Static).display = False
        self._apply_narrow()
        self._paint_header()
        self._fill_preview()
        self._fill_related()
        self.query_one("#scan-related-list", OptionList).focus()
        self._score_related()
        self._discover()

    def on_resize(self) -> None:
        self._apply_narrow()

    def _apply_narrow(self) -> None:
        self.set_class(self.size.width < 80, "-narrow")

    def action_close(self) -> None:
        self._discard_jobs()
        self.dismiss(None)

    def action_help(self) -> None:
        box = self.query_one("#scan-help", Static)
        cmd = self.query_one("#scan-cmd", Static)
        if box.display:
            box.display = False
            cmd.display = False
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
        rec = suggested_rec(self._ram)
        current = self._model_value() or rec.tag
        cmd.update(pull_command(current))
        cmd.display = True

    def action_toggle_overwrite(self) -> None:
        self._overwrite = not self._overwrite
        label = "o Overwrite existing date" if self._overwrite else "o Skip existing date"
        self.query_one("#scan-overwrite", Button).label = label

    def action_installed(self) -> None:
        if not self._local:
            self._status("Installed list is only for a local Ollama host.")
            return
        if self._installed is None:
            self._status("Still checking local Ollama.")
            return
        if not self._installed:
            rec = suggested_rec(self._ram)
            self._status(f"No vision models. {rec.pull_command()}")
            cmd = self.query_one("#scan-cmd", Static)
            cmd.update(rec.pull_command())
            cmd.display = True
            return
        self._show_model_chooser()

    def action_related(self) -> None:
        picker = self.query_one("#scan-related-list", OptionList)
        picker.focus()
        if not self.candidates:
            self._status("No other images in this folder.")

    def action_read(self) -> None:
        self._start_read(self._model_value(), persist=True)

    def action_cancel_read(self) -> None:
        self._cancel_ocr()
        self._busy = False
        self._job += 1
        self._status("Reading cancelled.")

    def action_accept(self) -> None:
        if self._busy:
            self._status("Wait for the reading, or cancel it.")
            return
        model = self._model_value()
        if model:
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
        self._discard_jobs()
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

    @on(OptionList.OptionSelected, "#scan-model-pick")
    def _model_picked(self, event: OptionList.OptionSelected) -> None:
        ident = event.option_id or ""
        if not ident.startswith("mdl-"):
            return
        index = int(ident.split("-", 1)[1])
        if index < 0 or index >= len(self._pick_models):
            return
        name = self._pick_models[index]
        self.query_one("#scan-model", Input).value = name
        self._persist_model(name)
        self._hide_model_chooser()
        self._status(f"Model set to {name}.")
        self._auto_started = True
        self._start_read(name, persist=False)

    @on(OptionList.OptionSelected, "#scan-related-list")
    def _related_picked(self, event: OptionList.OptionSelected) -> None:
        ident = event.option_id or ""
        if ident == "rel-none":
            path: Path | None = None
            reason = ""
        elif ident.startswith("rel-"):
            index = int(ident.split("-", 1)[1])
            if index < 0 or index >= len(self.candidates):
                return
            item = self.candidates[index]
            path = item.path
            reason = item.reason
        else:
            return
        if path == self.related:
            return
        self.related = path
        self.related_reason = reason
        self._invalidate_ocr()
        self._paint_header()
        self._status("Related source set. Press r to read it.")

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

    def _model_value(self) -> str:
        return self.query_one("#scan-model", Input).value.strip()

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

    def _fill_preview(self) -> None:
        path = self.subject
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

    def _fill_related(self) -> None:
        picker = self.query_one("#scan-related-list", OptionList)
        picker.clear_options()
        picker.add_option(Option("This photo only", id="rel-none"))
        for index, item in enumerate(self.candidates):
            picker.add_option(Option(item.line(), id=f"rel-{index}"))

    def _show_model_chooser(self) -> None:
        names = self._installed or []
        picker = self.query_one("#scan-model-pick", OptionList)
        picker.clear_options()
        loaded = set(self._loaded)
        self._pick_models = names
        for index, name in enumerate(names):
            mark = " (loaded)" if name in loaded else ""
            picker.add_option(Option(f"{name}{mark}", id=f"mdl-{index}"))
        picker.display = True
        picker.focus()

    def _hide_model_chooser(self) -> None:
        picker = self.query_one("#scan-model-pick", OptionList)
        picker.display = False

    def _status(self, message: str) -> None:
        self.query_one("#scan-status", Static).update(message)

    def _persist_model(self, model: str) -> None:
        save_scan_settings(ScanSettings(host=self.settings.host, model=model))
        self.settings = ScanSettings(host=self.settings.host, model=model)

    def _invalidate_ocr(self) -> None:
        self._cancel_ocr()
        self._busy = False
        self._job += 1
        self._engine = ""
        self._raw = ""
        self.bits = [item for item in self.bits if item.manual]
        if self.is_mounted:
            self._paint_chips()

    def _cancel_ocr(self) -> None:
        for worker in self.workers:
            if worker.group == "scan-ocr":
                worker.cancel()

    def _discard_jobs(self) -> None:
        self._cancel_ocr()
        self._busy = False
        self._job += 1
        self._discover_job += 1
        self._score_job += 1

    def _start_read(self, model: str, *, persist: bool) -> None:
        if self._busy:
            return
        name = model.strip()
        if not name:
            self._status("No model. Type notes as chips, or pick a vision model.")
            return
        if persist:
            self._persist_model(name)
        if not self._local and not self._remote_ok:
            self._remote_ok = True
            self._status(
                f"This host is not this computer. Press r again to send the image to "
                f"{self.settings.host}."
            )
            return
        source = self.related or self.subject
        self._busy = True
        self._job += 1
        self._status("Reading.")
        self._run_read(source, name, self._job)

    def _maybe_auto_read(self) -> None:
        if not self.is_mounted or self._auto_started or self._busy:
            return
        if not self._local:
            host = self.settings.host
            if self.settings.model.strip():
                self._status(f"Remote host {host}. Press r to confirm sending this image.")
            else:
                self._status(f"Remote host {host}. Pick or type a model, then r to confirm.")
            return
        if self._installed is None:
            return
        saved = self.settings.model.strip()
        if self._discover_error and not self._installed:
            self._status(self._discover_error)
            return
        if saved and saved in self._installed:
            self.query_one("#scan-model", Input).value = saved
            self._hide_model_chooser()
            self._auto_started = True
            self._start_read(saved, persist=False)
            return
        if saved and saved not in self._installed:
            self.query_one("#scan-model", Input).value = saved
            self._status(f"{saved} is not installed. Pick a vision model, or type notes.")
            if self._installed:
                self._show_model_chooser()
            return
        if self._installed:
            self._status("Pick a vision model. Notes work without one.")
            self._show_model_chooser()
            return
        rec = suggested_rec(self._ram)
        self._status(f"No vision models. {rec.pull_command()}")

    @work(thread=True, exclusive=True, group="scan-discover", exit_on_error=False)
    def _discover(self) -> None:
        job = self._discover_job
        if not self._local:
            self.app.call_from_thread(self._discover_done, job, [], [], "")
            return
        try:
            names = self.backend.list_vision_models()
        except BackendError as exc:
            self.app.call_from_thread(self._discover_done, job, [], [], str(exc))
            return
        loaded: list[str] = []
        try:
            loaded = self.backend.list_loaded_models()
        except BackendError:
            loaded = []
        self.app.call_from_thread(self._discover_done, job, names, loaded, "")

    def _discover_done(
        self,
        job: int,
        names: list[str],
        loaded: list[str],
        error: str,
    ) -> None:
        if job != self._discover_job or not self.is_mounted:
            return
        self._installed = names
        self._loaded = loaded
        self._discover_error = error
        self._paint_models()
        self._maybe_auto_read()

    @work(thread=True, exclusive=True, group="scan-candidates", exit_on_error=False)
    def _score_related(self) -> None:
        job = self._score_job
        rows = score_candidates(self.subject, self.files)
        self.app.call_from_thread(self._score_done, job, rows)

    def _score_done(self, job: int, rows: list[Candidate]) -> None:
        if job != self._score_job or not self.is_mounted:
            return
        self.candidates = rows
        self._fill_related()

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
        if job != self._job or not self.is_mounted:
            return
        self._busy = False
        if error or result is None:
            self._status(error or "Reading failed.")
            return
        self._engine = result.engine
        self._raw = result.raw
        manual = [item for item in self.bits if item.manual]
        self.bits = manual + bits_from_recognition(
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
