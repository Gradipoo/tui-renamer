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
    hidden: bool
    locked: bool

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


def review_date_changes(
    files: list[DateReviewFile],
    choices: list[int | None],
    filenames: bool,
    filesystem: bool,
    capture: bool,
    *,
    zone: tzinfo,
    prefix: bool,
    exiftool: str | None,
    birth_available: bool,
) -> tuple[list[DateChange], list[str], str]:
    proposed = []
    for file, choice in zip(files, choices, strict=True):
        name = file.path.name
        if filenames and not file.locked and choice is not None:
            candidate = file.dates.candidates[choice]
            if not candidate.blocked:
                name = file.dates.proposed_name(candidate, use_prefix=prefix)
        proposed.append(file.path.with_name(name))
    claimed = Counter(
        target.name.casefold() for file, target in zip(files, proposed) if target != file.path
    )
    plans = []
    descriptions = []
    for file, target, choice in zip(files, proposed, choices, strict=True):
        lines = [file.path.name]
        if file.hidden:
            lines.append("Hidden by current filter")
        if choice is None:
            lines.append("Skipped. Choose a source to include this file.")
        else:
            candidate = file.dates.candidates[choice]
            moment = candidate.moment.replace(microsecond=0)
            clock = _display(moment)
            lines.append(f"Chosen date  {clock}  ·  {candidate.label}")
            if candidate.warning:
                lines.append(candidate.warning)
            if candidate.blocked:
                lines.append("Skipped. Choose an unambiguous source.")
            else:
                stamp = moment.timestamp()
                modified = filesystem and (
                    file.modified is None or abs(stamp - file.modified) >= 0.001
                )
                birth = (
                    filesystem
                    and birth_available
                    and (file.birth is None or abs(stamp - file.birth) >= 1)
                )
                metadata = (
                    capture and candidate.capture and _metadata_available(file.path, exiftool)
                )
                if target != file.path:
                    lines.append(f"Filename  →  {target.name}")
                elif filenames and file.locked:
                    lines.append("Filename locked")
                if modified:
                    lines.append(f"Modified time  {_clock(file.modified, zone)}  →  {clock}")
                    lines.append(f"Access time  →  {clock}")
                if birth:
                    lines.append(f"Creation time  {_clock(file.birth, zone)}  →  {clock}")
                if filesystem and not birth_available:
                    lines.append("Creation time write unavailable")
                if capture and not metadata:
                    lines.append("Capture write unavailable for this source or file type")
                if metadata:
                    current = "\n".join(
                        f"  {item.label}  {_display(item.instant.astimezone(zone))}"
                        for item in file.facts.candidates
                        if item.capture
                        and item.instant is not None
                        and item.label != "Filename"
                        and not item.label.startswith("JSON")
                    )
                    lines.append(f"Embedded dates and offsets  →  {clock}")
                    lines.append(current or "  No embedded capture date found")
                renamed = target != file.path
                notes = renamed and _exists(notes_path_for(file.path))
                collision = renamed and (
                    claimed[target.name.casefold()] > 1
                    or _exists(target)
                    or (notes and _exists(notes_path_for(target)))
                )
                if file.revision is None:
                    lines.append("Skipped. File unavailable.")
                elif collision:
                    lines.append("Skipped. Destination filename or notes file is already claimed.")
                elif renamed or modified or birth or metadata:
                    if notes:
                        lines.append(f"Review notes  →  {notes_path_for(target).name}")
                    plans.append(
                        DateChange(file.path, target, moment, modified, birth, metadata, notes)
                    )
                else:
                    lines.append("No changes with these options")
        descriptions.append("\n".join(lines))
    counts = (
        f"{len(plans)} of {len(files)} files · "
        f"{sum(file.hidden for file in files)} hidden in review\n"
        f"{sum(item.rename for item in plans)} names · "
        f"{sum(item.modified for item in plans)} modified times · "
        f"{sum(item.birth for item in plans)} creation times · "
        f"{sum(item.capture for item in plans)} embedded writes"
    )
    return plans, descriptions, counts


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
