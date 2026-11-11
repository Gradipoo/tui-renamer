"""Filters distinguish capture metadata from inferred and filesystem dates."""

from datetime import datetime, timezone

from tui_renamer.core import DateCandidate, FileFacts
from tui_renamer.table_view import TableView, embedded_capture, matches_view


def test_embedded_capture_excludes_filename_json_and_file_times() -> None:
    moment = datetime(2020, 1, 1, tzinfo=timezone.utc)
    facts = FileFacts(
        candidates=[
            DateCandidate("Filename", "", moment, capture=True),
            DateCandidate("JSON photoTakenTime", "", moment, capture=True),
            DateCandidate("birth", "", moment),
            DateCandidate("mtime", "", moment),
        ]
    )
    assert embedded_capture(facts) is None
    facts.candidates.append(DateCandidate("QuickTime creation", "", moment, capture=True))
    assert embedded_capture(facts) == moment


def test_filters_intersect_and_extension_is_case_insensitive() -> None:
    view = TableView(frozenset({"missing_capture", "changed", "pending", "warning"}), ".JPG")
    flags = dict(warning=True, changed=True, pending=True)
    assert matches_view(view, ".jpg", FileFacts(), **flags)
    assert not matches_view(view, ".png", FileFacts(), **flags)
    assert not matches_view(view, ".jpg", FileFacts(), **(flags | {"pending": False}))
    assert not matches_view(view, ".jpg", FileFacts(), **(flags | {"warning": False}))


def test_date_disagreement_filter_uses_capture_evidence() -> None:
    moment = datetime(2020, 1, 1, tzinfo=timezone.utc)
    facts = FileFacts(
        winner_instant=moment,
        winner_source="E",
        candidates=[
            DateCandidate("EXIF", "", moment, capture=True),
            DateCandidate("Filename", "", datetime(2021, 1, 1, tzinfo=timezone.utc), capture=True),
        ],
    )
    view = TableView(frozenset({"disagreement"}))
    assert matches_view(view, ".jpg", facts, warning=False, changed=False, pending=False)
    facts.candidates.pop()
    assert not matches_view(view, ".jpg", facts, warning=False, changed=False, pending=False)
