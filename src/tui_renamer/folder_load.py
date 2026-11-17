"""Read a complete folder snapshot without touching Textual or writing files."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from threading import Event
from time import monotonic

from tui_renamer import preview
from tui_renamer.core import FileFacts, RenameRules, list_files, resolve_tz
from tui_renamer.metadata import inspect_file


@dataclass
class FolderSnapshot:
    files: list[Path]
    facts: dict[Path, FileFacts]
    resolved_paths: set[Path]


def load_folder(
    directory: Path,
    rules: RenameRules,
    cancel: Event,
    progress: Callable[[int, int], None],
    *,
    reader: Callable[[Path, RenameRules, tzinfo], FileFacts] = inspect_file,
    strict: bool = True,
) -> FolderSnapshot | None:
    """Read at most four files at once. Cancel stops scheduling further reads.

    In-flight filesystem or external-tool reads may finish after cancellation.
    Their results stay local to this call and are never installed in the app.
    """
    if cancel.is_set():
        return None
    files = list_files(directory, strict=strict)
    if cancel.is_set():
        return None
    progress(0, len(files))
    if any(path.suffix.lower() in preview.HEIF_SUFFIXES for path in files):
        preview.heif_ready()
    preview.PILImage.init()
    zone = resolve_tz(rules.source_tz)
    facts: dict[Path, FileFacts] = {}
    pending_paths = iter(files)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rn-metadata")
    pending = {}
    last_progress = monotonic()
    try:
        for path in pending_paths:
            if cancel.is_set():
                return None
            pending[pool.submit(reader, path, rules, zone)] = path
            if len(pending) == 4:
                break
        while pending:
            if cancel.is_set():
                return None
            completed, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in completed:
                path = pending.pop(future)
                facts[path] = future.result()
                if cancel.is_set():
                    return None
                now = monotonic()
                if len(facts) == 1 or now - last_progress >= 0.05 or len(facts) == len(files):
                    progress(len(facts), len(files))
                    last_progress = now
                next_path = next(pending_paths, None)
                if next_path is not None:
                    pending[pool.submit(reader, next_path, rules, zone)] = next_path
        if cancel.is_set():
            return None
        return FolderSnapshot(files, facts, {path.resolve() for path in files})
    finally:
        # Running reads cannot be interrupted. Never wait for them on cancellation.
        pool.shutdown(wait=False, cancel_futures=True)
