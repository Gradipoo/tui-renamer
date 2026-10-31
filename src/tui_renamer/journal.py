"""Session undo for renames and metadata writes.

The stack lives in memory. JPEG and TIFF EXIF is restored from the previous
piexif dump. exiftool targets are copied aside first. Quit deletes that folder.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tui_renamer.metadata import PIEXIF_AVAILABLE, piexif, snapshot_mode


@dataclass
class RenameOp:
    src: Path
    dst: Path


@dataclass
class MetaOp:
    path: Path
    atime: float
    mtime: float
    birth: float | None
    mode: str
    exif_blob: bytes | None = None
    had_exif: bool = False
    backup: Path | None = None
    copy_failed: bool = False


@dataclass
class Batch:
    kind: str
    ops: list[RenameOp | MetaOp] = field(default_factory=list)


@dataclass
class UndoResult:
    kind: str
    restored: int
    notes: list[str]


class Journal:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.batches: list[Batch] = []
        self._seq = 0

    def snapshot(self, path: Path, *, mode: str | None = None) -> MetaOp:
        """Record times, and EXIF bytes or a file copy, before a write.

        mode overrides the default (piexif, copy, or times). mtime-only
        updates pass mode='times' so undo does not rewrite the file bytes.
        """
        stat = path.stat()
        birth = getattr(stat, "st_birthtime", None)
        if birth is None and os.name == "nt":
            birth = stat.st_ctime
        if mode is None:
            mode = snapshot_mode(path)
        op = MetaOp(
            path=path,
            atime=stat.st_atime,
            mtime=stat.st_mtime,
            birth=birth,
            mode=mode,
        )
        if mode == "piexif" and PIEXIF_AVAILABLE and piexif is not None:
            try:
                op.exif_blob = piexif.dump(piexif.load(str(path)))
                op.had_exif = True
            except Exception:
                op.had_exif = False
        elif mode == "copy":
            self._seq += 1
            dest = self.temp_dir / f"{self._seq}-{path.name}"
            try:
                shutil.copy2(path, dest)
                op.backup = dest
            except OSError:
                op.copy_failed = True
        return op

    def push(self, batch: Batch) -> None:
        if batch.ops:
            self.batches.append(batch)

    def undo(
        self, *, restore_birth: Callable[[Path, float], None] | None = None
    ) -> UndoResult | None:
        """Roll back the last batch. Returns None when the stack is empty."""
        if not self.batches:
            return None
        batch = self.batches.pop()
        notes: list[str] = []
        restored = 0
        for op in reversed(batch.ops):
            if isinstance(op, RenameOp):
                ok = _undo_rename(op, notes)
            else:
                ok = _undo_meta(op, notes, restore_birth)
            if ok:
                restored += 1
        return UndoResult(batch.kind, restored, notes)

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _undo_rename(op: RenameOp, notes: list[str]) -> bool:
    if not op.dst.exists():
        notes.append(f"{op.dst.name} is missing, so it was not restored")
        return False
    try:
        op.dst.rename(op.src)
    except OSError as exc:
        notes.append(f"{op.dst.name}: {exc}")
        return False
    return True


def _undo_meta(
    op: MetaOp, notes: list[str], restore_birth: Callable[[Path, float], None] | None
) -> bool:
    if op.copy_failed:
        notes.append(f"{op.path.name}: metadata bytes could not be restored")
    elif op.backup is not None:
        try:
            shutil.copy2(op.backup, op.path)
        except OSError as exc:
            notes.append(f"{op.path.name}: {exc}")
            return False
    elif op.mode == "piexif" and PIEXIF_AVAILABLE and piexif is not None:
        try:
            if op.had_exif and op.exif_blob is not None:
                piexif.insert(op.exif_blob, str(op.path))
            elif not op.had_exif:
                piexif.remove(str(op.path))
        except Exception as exc:
            notes.append(f"{op.path.name}: {exc}")
            return False
    try:
        os.utime(op.path, (op.atime, op.mtime))
    except OSError as exc:
        notes.append(f"{op.path.name}: {exc}")
        return False
    if op.birth is not None and restore_birth is not None:
        try:
            restore_birth(op.path, op.birth)
        except Exception as exc:
            notes.append(f"{op.path.name} birth time: {exc}")
    return True
