"""Review and execute the exact date changes chosen for a batch."""

from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from tui_renamer.core import FileFacts, format_offset
from tui_renamer.journal import Batch, Journal, RenameOp, move_to_free_path
from tui_renamer.metadata import (
    IMAGE_EXTENSIONS,
    PIEXIF_AVAILABLE,
    can_write_metadata,
    reconcile_metadata_target,
    set_birthtime,
    write_file_metadata_and_offset,
)
from tui_renamer.reconcile import Reconciliation
from tui_renamer.scan_notes import notes_path_for


def file_revision(path: Path) -> tuple[int, ...] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class DateReviewFile:
    path: Path
    facts: FileFacts
    dates: Reconciliation
    revision: tuple[int, ...] | None
    sidecar_revision: tuple[int, ...] | None
    birth: float | None
    modified: float | None
    locked: bool
    embedded: dict[str, str | None]

    def is_current(self) -> bool:
        return (
            self.revision is not None
            and self.revision == file_revision(self.path)
            and (
                self.facts.sidecar is None
                or self.sidecar_revision == file_revision(self.facts.sidecar)
            )
        )


@dataclass(frozen=True)
class DateChange:
    path: Path
    target: Path
    moment: datetime | None = None
    modified: bool = False
    birth: bool = False
    capture: bool = False
    notes: bool = False

    @property
    def rename(self) -> bool:
        return self.target != self.path


def _clock(stamp: float | None, zone: tzinfo) -> str:
    if stamp is None:
        return "unavailable"
    return _display(datetime.fromtimestamp(stamp, zone))


def _display(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d %H:%M:%S} {format_offset(moment)}"


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _metadata_available(path: Path, exiftool: str | None) -> bool:
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return PIEXIF_AVAILABLE
    return bool(exiftool) and can_write_metadata(path)


@dataclass(frozen=True)
class DateValueDiff:
    before: tuple[int, int]
    after: tuple[int, int]


@dataclass(frozen=True)
class DateReviewRow:
    detail: str
    date: str = ""
    source: str = ""
    status: str = "Needs review"
    change: DateChange | None = None
    diffs: tuple[DateValueDiff, ...] = ()

    @property
    def fields(self) -> str:
        change = self.change
        if change is None:
            return "—"
        return ", ".join(
            label
            for enabled, label in (
                (change.rename, "Name"),
                (change.modified, "Mtime"),
                (change.birth, "Birth"),
                (change.capture, "Capture"),
            )
            if enabled
        )


def review_date_changes(
    files: list[DateReviewFile],
    options: list[tuple[int | None, bool, bool, bool, bool]],
    *,
    zone: tzinfo,
    prefix: bool,
    exiftool: str | None,
    birth_available: bool,
) -> list[DateReviewRow]:
    proposed = []
    for file, (choice, filenames, _, _, _) in zip(files, options, strict=True):
        name = file.path.name
        if filenames and not file.locked and choice is not None:
            name = file.dates.proposed_name(file.dates.candidates[choice], use_prefix=prefix)
        proposed.append(file.path.with_name(name))
    claimed = Counter(
        target.name.casefold()
        for file, target in zip(files, proposed, strict=True)
        if target != file.path
    )
    rows = []
    for file, target, (choice, filenames, filesystem, creation, capture) in zip(
        files, proposed, options, strict=True
    ):
        lines = [file.path.name]
        diffs: list[DateValueDiff] = []

        def add_diff(label: str, old: str, new: str) -> None:
            header = f"{label}  "
            separator = "  →  " if old else "→  "
            before = sum(len(line) + 1 for line in lines) + len(header)
            after = before + len(old) + len(separator)
            diffs.append(DateValueDiff((before, before + len(old)), (after, after + len(new))))
            lines.append(f"{header}{old}{separator}{new}")

        if file.revision is None:
            rows.append(DateReviewRow("File unavailable or symbolic link."))
            continue
        if choice is None:
            recommended = file.dates.recommended
            if recommended is None:
                if file.dates.filename and file.dates.filename.precision != "second":
                    lines.append(
                        "Incomplete filename date conflicts with other dates "
                        "or has no complete match."
                    )
                lines.append("Choose a date source with I, or skip this file.")
                rows.append(DateReviewRow("\n".join(lines)))
            else:
                if recommended.warning:
                    lines.append(recommended.warning)
                lines.append("Choose or confirm a date source with I.")
                rows.append(
                    DateReviewRow("\n".join(lines), _display(recommended.moment), recommended.label)
                )
            continue
        candidate = file.dates.candidates[choice]
        moment = candidate.moment.replace(microsecond=0)
        clock = _display(moment)
        lines.append(f"Chosen date  {clock}  ·  {candidate.label}")
        if candidate.warning:
            lines.append(candidate.warning)
        if candidate.blocked:
            lines.append("Choose an unambiguous source with I.")
            rows.append(DateReviewRow("\n".join(lines), clock, candidate.label))
            continue
        stamp = moment.timestamp()
        modified = filesystem and (file.modified is None or abs(stamp - file.modified) >= 0.001)
        birth = (
            creation and birth_available and (file.birth is None or abs(stamp - file.birth) >= 1)
        )
        embedded_changes = [
            (tag, value, desired)
            for tag, value in file.embedded.items()
            if value != (desired := reconcile_metadata_target(file.path, tag, moment))
        ]
        metadata = capture and candidate.capture and bool(embedded_changes)
        if target != file.path:
            add_diff("Filename", file.path.name, target.name)
        elif filenames and file.locked:
            lines.append("Filename has a staged or locked name")
        if modified:
            add_diff("Modified time", _clock(file.modified, zone), clock)
            add_diff("Access time", "", clock)
        if birth:
            add_diff("Creation time", _clock(file.birth, zone), clock)
        if creation and not birth_available:
            lines.append("Creation time write unavailable")
        if capture and not _metadata_available(file.path, exiftool):
            lines.append("Capture write unavailable for this file type")
        if metadata:
            for tag, value, new in embedded_changes:
                add_diff(tag, value or "missing", new)
        renamed = target != file.path
        notes = renamed and _exists(notes_path_for(file.path))
        collision = renamed and (
            claimed[target.name.casefold()] > 1
            or _exists(target)
            or (notes and _exists(notes_path_for(target)))
        )
        status = "Needs review"
        change = None
        if collision:
            lines.append(
                "Destination occupied. Choose a different source or exclude Filename with I."
            )
        elif capture and not candidate.capture and embedded_changes:
            lines.append(
                "This source cannot set capture dates. "
                "Choose another source or exclude Capture with I."
            )
        elif renamed or modified or birth or metadata:
            if notes:
                add_diff(
                    "Review notes", notes_path_for(file.path).name, notes_path_for(target).name
                )
            change = DateChange(file.path, target, moment, modified, birth, metadata, notes)
            status = "Update"
        else:
            lines.append("No changes with these options")
            status = "Match"
        rows.append(
            DateReviewRow("\n".join(lines), clock, candidate.label, status, change, tuple(diffs))
        )
    return rows


def apply_date_changes(
    changes: list[DateChange],
    journal: Journal,
    *,
    zone: tzinfo,
    exiftool: str | None,
    setfile: str | None,
) -> tuple[Batch, dict[Path, Path], list[str], list[str]]:
    batch = Batch("reconcile")
    moved: dict[Path, Path] = {}
    details: list[str] = []
    errors: list[str] = []
    for change in changes:
        path = change.path
        if change.rename:
            try:
                move_to_free_path(path, change.target)
            except OSError as exc:
                errors.append(f"{path.name}: rename failed ({exc})")
                continue
            batch.ops.append(RenameOp(path, change.target, require_free=True))
            moved[path] = change.target
            details.append(f"Renamed {path.name} → {change.target.name}")
            if change.notes:
                old_notes = notes_path_for(path)
                new_notes = notes_path_for(change.target)
                try:
                    move_to_free_path(old_notes, new_notes)
                except OSError as exc:
                    errors.append(f"{old_notes.name}: notes rename failed ({exc})")
                else:
                    batch.ops.append(RenameOp(old_notes, new_notes, require_free=True))
            path = change.target
        if not (change.modified or change.birth or change.capture) or change.moment is None:
            continue
        try:
            op = journal.snapshot(path, mode=None if change.capture else "times")
        except OSError as exc:
            errors.append(f"{path.name}: undo snapshot failed ({exc})")
            continue
        if op.copy_failed:
            errors.append(f"{path.name}: date writes skipped because backup failed")
            continue
        batch.ops.append(op)
        if change.capture:
            if write_file_metadata_and_offset(path, change.moment, exiftool=exiftool):
                details.append(f"Embedded dates written for {path.name}")
            else:
                errors.append(f"{path.name}: embedded write failed")
        # Embedded writes can change mtime. Restore both times if filesystem writes are off.
        if change.modified or change.capture:
            stamp = change.moment.timestamp()
            times = (stamp, stamp) if change.modified else (op.atime, op.mtime)
            try:
                os.utime(path, times)
            except OSError as exc:
                errors.append(f"{path.name}: filesystem times could not be set ({exc})")
            else:
                if change.modified:
                    details.append(f"Modified and access times set for {path.name}")
        if change.birth or (change.capture and op.birth is not None and birth_supported(setfile)):
            moment = change.moment if change.birth else datetime.fromtimestamp(op.birth, zone)
            if set_birthtime(path, moment, setfile=setfile):
                if change.birth:
                    details.append(f"Creation time set for {path.name}")
            else:
                errors.append(f"{path.name}: creation time could not be set")
    return batch, moved, details, errors


def birth_supported(setfile: str | None) -> bool:
    return os.name == "nt" or (sys.platform == "darwin" and bool(setfile))
