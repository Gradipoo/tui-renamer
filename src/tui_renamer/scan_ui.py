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

from tui_renamer.core import RenameRules
from tui_renamer.preview import Preview, ThumbCache, build_preview, load_image_widget
from tui_renamer.scan_backend import BackendError, Recognition, ScanBackend
from tui_renamer.scan_bits import (
    MAX_CHIPS,
    Bit,
    PendingScan,
    ScanSession,
    StageResult,
    bits_from_recognition,
    capture_from_bits,
    description_from_bits,
    format_scan_parts,
    join_scan_name,
    selected_labels,
    strip_accepted_suffix,
)
from tui_renamer.scan_candidates import Candidate, FileClocks, score_candidates, stat_clocks
from tui_renamer.scan_config import ScanSettings, host_is_local, save_scan_settings
from tui_renamer.scan_dates import parse_date_text
from tui_renamer.scan_models import pull_command, setup_lines, suggested_rec, total_ram_bytes
from tui_renamer.scan_notes import load_notes, notes_path_for


class ReviewScreen(ModalScreen[StageResult | None]):
    """Analyze the current photo. A related file is optional."""

    BINDINGS = [
        Binding("escape", "escape", show=False),
        Binding("q", "back", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("r", "read", show=False),
        Binding("R", "read_related", show=False),
        Binding("x", "cancel_read", show=False),
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
        base_stem: str = "",
        base_ext: str = "",
        locked: bool = False,
        accepted_suffix: list[str] | None = None,
        session: ScanSession | None = None,
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
        self.base_stem = base_stem
        self.base_ext = base_ext
        self.locked = locked
        self.accepted_suffix = list(accepted_suffix or [])
        self.session = session
        self.highlight: Path | None = None
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
        self._preview_job = 0
        self._name_seq = 0
        self._restored = False
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
                    with Horizontal(id="scan-previews"):
                        yield Vertical(id="scan-orig-slot")
                        yield Vertical(id="scan-rel-slot")
                    yield Static("", id="scan-name-preview", markup=False)
                    yield Static("", id="scan-source", markup=False)
                    yield Horizontal(id="scan-chips")
                    yield Input(placeholder="add a name or date chip", id="scan-add")
                    yield OptionList(id="scan-model-pick")
                    yield Input(placeholder="model override, Enter reads", id="scan-model")
                with VerticalScroll(id="scan-right"):
                    yield Static("Related (highlight to preview, R to read)", markup=False)
                    yield OptionList(id="scan-related-list")
            with Horizontal(id="scan-actions"):
                yield Button(
                    "r Original",
                    id="scan-read",
                    compact=True,
                    tooltip="Read the original photo",
                )
                yield Button(
                    "R Related",
                    id="scan-read-related",
                    compact=True,
                    tooltip="Read the highlighted related photo",
                )
                yield Button(
                    "x Cancel",
                    id="scan-cancel",
                    compact=True,
                    tooltip="Cancel the reading",
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
                    tooltip="Close and keep the staged name (q or Esc)",
                )
            yield Static("", id="scan-help", markup=False)
            yield Static("", id="scan-cmd", markup=False)

    def on_mount(self) -> None:
        self.query_one("#scan-model", Input).value = self.settings.model
        self.query_one("#scan-model-pick", OptionList).display = False
        self.query_one("#scan-help", Static).display = False
        self.query_one("#scan-cmd", Static).display = False
        self._apply_narrow()
        self._restore_notes()
        self._paint_chips()
        self._paint_header()
        self._fill_slot(self.query_one("#scan-orig-slot"), self.subject, "orig")
        self._fill_slot(self.query_one("#scan-rel-slot"), None, "rel")
        self._fill_related()
        self._paint_name_preview()
        self.query_one("#scan-left").focus()
        self._score_related()
        self._discover()

    def on_resize(self) -> None:
        self._apply_narrow()

    def _apply_narrow(self) -> None:
        self.set_class(self.size.width < 80, "-narrow")

    def action_close(self) -> None:
        self._discard_jobs()
        self.dismiss(self._stage_result())

    def action_escape(self) -> None:
        if self._blur_input():
            return
        self.action_close()

    def action_back(self) -> None:
        if self._focused_input() is not None:
            return
        self.action_close()

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def _focused_input(self) -> Input | None:
        focused = self.focused
        if isinstance(focused, Input):
            return focused
        return None

    def _blur_input(self) -> bool:
        box = self._focused_input()
        if box is None:
            return False
        self.query_one("#scan-left").focus()
        return True

    def _move_cursor(self, delta: int) -> None:
        if self._focused_input() is not None:
            return
        picker = self.query_one("#scan-model-pick", OptionList)
        if picker.display and self._focus_in(picker):
            self._move_options(picker, delta)
            return
        related = self.query_one("#scan-related-list", OptionList)
        if self._focus_in(related):
            self._move_options(related, delta)
            return
        chips = list(self.query(".scan-chip"))
        current = self._chip_from_focus()
        if chips and current is not None:
            index = chips.index(current)
            nxt = index + delta
            if 0 <= nxt < len(chips):
                self._focus_widget(chips[nxt])
            return
        if chips:
            target = chips[0] if delta > 0 else chips[-1]
            self._focus_widget(target)
            return
        related.focus()
        self._move_options(related, delta)

    def _focus_in(self, widget) -> bool:
        focused = self.focused
        if focused is None:
            return False
        return focused is widget or widget in focused.ancestors

    def _chip_from_focus(self) -> Button | None:
        focused = self.focused
        if focused is None:
            return None
        if focused.has_class("scan-chip") and isinstance(focused, Button):
            return focused
        if focused.has_class("scan-meta"):
            parent = focused.parent
            if parent is not None:
                matches = list(parent.query(".scan-chip"))
                if matches and isinstance(matches[0], Button):
                    return matches[0]
        return None

    def _move_options(self, listing: OptionList, delta: int) -> None:
        if delta > 0:
            listing.action_cursor_down()
        else:
            listing.action_cursor_up()
        listing.scroll_to_highlight()

    def _focus_widget(self, widget) -> None:
        widget.focus()
        widget.scroll_visible()

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
        self._start_read(self._model_value(), source=self.subject, persist=True)

    def action_read_related(self) -> None:
        if self.highlight is None:
            self._status("Highlight a related file, then press R to read it.")
            return
        self.related = self.highlight
        match = next((item for item in self.candidates if item.path == self.highlight), None)
        self.related_reason = match.reason if match else "manual"
        self._paint_header()
        self._start_read(self._model_value(), source=self.highlight, persist=True)

    def action_cancel_read(self) -> None:
        self._cancel_ocr()
        self._busy = False
        self._job += 1
        self._status("Reading cancelled.")

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
        self._start_read(name, source=self.subject, persist=False)

    @on(OptionList.OptionHighlighted, "#scan-related-list")
    def _related_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        ident = event.option_id or ""
        path: Path | None = None
        if ident.startswith("rel-") and ident != "rel-none":
            index = int(ident.split("-", 1)[1])
            if 0 <= index < len(self.candidates):
                path = self.candidates[index].path
        if path == self.highlight:
            return
        self.highlight = path
        self._preview_job += 1
        job = self._preview_job
        self._fill_related_preview(path, job)
        if path is None:
            self._status("Original photo. r reads this file.")
        else:
            self._status(f"Preview {path.name}. r reads original. R reads this file.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id or ""
        if ident.startswith("scan-bit-"):
            index = int(ident.rsplit("-", 1)[-1])
            if 0 <= index < len(self.bits):
                self._toggle_name(self.bits[index])
            return
        if ident.startswith("scan-meta-"):
            index = int(ident.rsplit("-", 1)[-1])
            if 0 <= index < len(self.bits):
                self._toggle_meta(self.bits[index])
            return
        run = {
            "scan-read": self.action_read,
            "scan-read-related": self.action_read_related,
            "scan-cancel": self.action_cancel_read,
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
        self._paint_name_preview()
        self._sync_host()

    def _toggle_name(self, item: Bit) -> None:
        was = item.use_name
        item.toggle_name()
        if item.use_name and not was:
            self._name_seq += 1
            item.name_order = self._name_seq
        self._paint_chips()
        self._paint_name_preview()
        self._sync_host()

    def _toggle_meta(self, item: Bit) -> None:
        item.toggle_meta()
        self._paint_chips()

    def _restore_notes(self) -> None:
        if self.session is not None:
            self.bits = self.session.bits
            self._engine = self.session.engine
            self._raw = self.session.raw
            self._overwrite = self.session.overwrite
            if self.session.related_name:
                match = next(
                    (path for path in self.files if path.name == self.session.related_name),
                    None,
                )
                self.related = match
                self.related_reason = self.session.related_reason
            self._finish_restore()
            return
        notes = load_notes(notes_path_for(self.subject))
        if notes is None:
            return
        if notes.name_suffix and not self.accepted_suffix:
            self.accepted_suffix = list(notes.name_suffix)
        if not notes.bits:
            return
        self.bits = notes.bits
        self._engine = notes.engine
        self._raw = notes.raw_transcription
        self._finish_restore()

    def _finish_restore(self) -> None:
        seq = 0
        for item in self.bits:
            if item.use_name and item.name_order <= 0:
                seq += 1
                item.name_order = seq
        self._name_seq = max((item.name_order for item in self.bits), default=0)
        self._restored = True
        self._auto_started = True

    def _name_rules(self) -> RenameRules:
        rules = getattr(self.app, "rules", None)
        return rules if isinstance(rules, RenameRules) else RenameRules()

    def _paint_name_preview(self) -> None:
        box = self.query_one("#scan-name-preview", Static)
        if self.locked:
            name = join_scan_name(self.base_stem, self.base_ext, [], fallback=self.subject.name)
            box.update(f"Locked: {name}")
            return
        stem = strip_accepted_suffix(self.base_stem, self.accepted_suffix)
        name = join_scan_name(
            stem,
            self.base_ext,
            format_scan_parts(selected_labels(self.bits), self._name_rules()),
            fallback=self.subject.name,
        )
        box.update(f"New: {name}")

    def _paint_header(self) -> None:
        related = self.related.name if self.related else "none"
        self.query_one("#scan-photo", Static).update(
            f"{self.subject.name}  last related read {related}"
        )
        self.query_one("#scan-source", Static).update(
            f"r reads {self.subject.name}. R reads the highlighted related file."
        )

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
            if item.use_name:
                button.add_class("-on")
            pieces: list[Button] = [button]
            if item.can_meta():
                meta = Button(
                    "meta",
                    id=f"scan-meta-{self._chip_gen}-{index}",
                    compact=True,
                    tooltip="Use this chip for metadata, not the filename",
                )
                meta.add_class("scan-meta")
                if item.use_meta:
                    meta.add_class("-on")
                pieces.append(meta)
            row.mount(Horizontal(*pieces, classes="scan-chip-row"))

    def _session_record(self) -> ScanSession:
        return ScanSession(
            bits=self.bits,
            suffix_parts=selected_labels(self.bits),
            engine=self._engine,
            raw=self._raw,
            model=self.settings.model,
            related_name=self.related.name if self.related else None,
            related_reason=self.related_reason,
            overwrite=self._overwrite,
        )

    def _stage_result(self) -> StageResult:
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
        return StageResult(
            message=f"Staged {self.subject.name}. Apply writes the disk.{extra}",
            subject=self.subject,
            suffix_parts=selected_labels(self.bits),
            pending=pending,
            session=self._session_record(),
        )

    def _sync_host(self) -> None:
        host = self.app
        stage = getattr(host, "stage_scan_review", None)
        if callable(stage):
            stage(self._stage_result(), refresh=True)

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

    def _fill_slot(self, slot, path: Path | None, kind: str) -> None:
        slot.remove_children()
        if path is None:
            slot.mount(Static("No related highlight", markup=False))
            return
        result: Preview = build_preview(path, self.cache)
        slot.mount(Static(result.caption, id=f"scan-cap-{kind}-{self._preview_job}", markup=False))
        widget_cls = load_image_widget()
        if result.show_image and result.image is not None and widget_cls is not None:
            try:
                slot.mount(widget_cls(result.image, id=f"scan-img-{kind}-{self._preview_job}"))
            except Exception:
                slot.mount(Static(result.body or result.caption, markup=False))
        elif result.body:
            slot.mount(Static(result.body, markup=False))

    def _fill_related_preview(self, path: Path | None, job: int) -> None:
        self._run_related_preview(path, job)

    @work(thread=True, exclusive=True, group="scan-preview", exit_on_error=False)
    def _run_related_preview(self, path: Path | None, job: int) -> None:
        result = None if path is None else build_preview(path, self.cache)
        self.app.call_from_thread(self._related_preview_done, job, path, result)

    def _related_preview_done(
        self,
        job: int,
        path: Path | None,
        result: Preview | None,
    ) -> None:
        if job != self._preview_job or not self.is_mounted:
            return
        slot = self.query_one("#scan-rel-slot")
        slot.remove_children()
        if path is None or result is None:
            slot.mount(Static("No related highlight", markup=False))
            return
        slot.mount(Static(result.caption, id=f"scan-cap-rel-{job}", markup=False))
        widget_cls = load_image_widget()
        if result.show_image and result.image is not None and widget_cls is not None:
            try:
                slot.mount(widget_cls(result.image, id=f"scan-img-rel-{job}"))
            except Exception:
                slot.mount(Static(result.body or result.caption, markup=False))
        elif result.body:
            slot.mount(Static(result.body, markup=False))

    def _fill_related(self) -> None:
        picker = self.query_one("#scan-related-list", OptionList)
        keep = self.highlight
        picker.clear_options()
        picker.add_option(Option("This photo only", id="rel-none"))
        for index, item in enumerate(self.candidates):
            picker.add_option(Option(item.line(), id=f"rel-{index}"))
        chosen: Path | None = None
        highlight_at = 0
        if keep is not None:
            for index, item in enumerate(self.candidates):
                if item.path == keep:
                    chosen = keep
                    highlight_at = index + 1
                    break
        if chosen is None and self.candidates:
            chosen = self.candidates[0].path
            highlight_at = 1
        self.highlight = chosen
        picker.highlighted = highlight_at
        self._preview_job += 1
        self._fill_related_preview(chosen, self._preview_job)

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
        self._preview_job += 1

    def _start_read(self, model: str, *, source: Path, persist: bool) -> None:
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
        self._busy = True
        self._job += 1
        self._status(f"Reading {source.name}.")
        self._run_read(source, name, self._job)

    def _maybe_auto_read(self) -> None:
        if not self.is_mounted or self._auto_started or self._busy:
            return
        if self._restored:
            self._status("Restored notes. r reads original. R reads related.")
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
            self._start_read(saved, source=self.subject, persist=False)
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
        clocks = self._clocks_for_files()
        rows = score_candidates(self.subject, self.files, clocks=clocks, size_cache={})
        self.app.call_from_thread(self._score_done, job, rows)

    def _clocks_for_files(self) -> dict[Path, FileClocks]:
        host = self.app
        cached = getattr(host, "_facts", {})
        clocks: dict[Path, FileClocks] = {}
        for path in self.files:
            capture = None
            for key, facts in cached.items():
                if not isinstance(key, tuple) or key[0] != str(path):
                    continue
                if getattr(facts, "has_exif", False):
                    for cand in getattr(facts, "candidates", []):
                        instant = getattr(cand, "instant", None)
                        if getattr(cand, "capture", False) and instant is not None:
                            if instant.tzinfo is not None:
                                capture = instant
                                break
                break
            fs = stat_clocks(path)
            clocks[path] = FileClocks(capture=capture, mtime=fs.mtime, birth=fs.birth)
        return clocks

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
        kept = [item for item in self.bits if item.manual or item.source != source]
        added = bits_from_recognition(
            transcription=result.transcription,
            dates=result.dates,
            tokens=result.tokens,
            people_present=result.people_present,
            people_count=result.people_count,
            source=source,
            model=result.model,
        )
        self.bits = (kept + added)[:MAX_CHIPS]
        self._paint_chips()
        self._paint_name_preview()
        self._status("Name chips stage the New column. Meta is separate. Esc keeps the name.")
