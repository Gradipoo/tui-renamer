"""File-table filtering and sorting, independent of table row positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tui_renamer.core import FileFacts, disagreements

FILTERS = (
    ("missing_capture", "Missing embedded capture date"),
    ("disagreement", "Date disagreement"),
    ("warning", "Warnings"),
    ("changed", "Changed filename"),
    ("pending", "Pending metadata"),
)
SORTS = (
    ("Filename", "name"),
    ("Capture date", "capture"),
    ("New name", "new"),
    ("Warnings first", "warning"),
)


@dataclass(frozen=True)
class TableView:
    filters: frozenset[str] = frozenset()
    extension: str = ""
    sort: str = "name"
    descending: bool = False


def embedded_capture(facts: FileFacts) -> datetime | None:
    """Embedded photo or video date, excluding filename, JSON, and file times."""
    return next(
        (
            item.instant
            for item in facts.candidates
            if item.capture
            and item.instant is not None
            and item.label != "Filename"
            and not item.label.startswith("JSON ")
        ),
        None,
    )


def matches_view(
    view: TableView,
    suffix: str,
    facts: FileFacts,
    *,
    warning: bool,
    changed: bool,
    pending: bool,
) -> bool:
    extension = view.extension.strip().lower().lstrip(".")
    if extension and suffix.lower().lstrip(".") != extension:
        return False
    flags = {
        "missing_capture": embedded_capture(facts) is None,
        "disagreement": bool(disagreements(facts)),
        "warning": warning,
        "changed": changed,
        "pending": pending,
    }
    return all(flags.get(key, False) for key in view.filters)
