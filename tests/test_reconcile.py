from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tui_renamer.core import DateCandidate, FileFacts
from tui_renamer.reconcile import reconcile_dates

UTC = timezone.utc


def fact(label, iso, *, capture=False):
    return DateCandidate(
        label, "", datetime.fromisoformat(iso).replace(tzinfo=UTC), capture=capture
    )


def test_reconcile_uses_actual_filename_and_preserves_rest():
    facts = FileFacts(
        candidates=[
            fact("Filename", "1999-01-01T00:00:00"),
            fact("mtime", "2024-02-01T00:00:00"),
            fact("EXIF", "2023-02-01T00:00:00", capture=True),
        ]
    )
    plan = reconcile_dates("My_2024-01-01-120000-01.Photo.JPG", facts, UTC)

    assert plan.recommended is plan.earliest
    assert plan.candidates[:2] == (plan.earliest, plan.filename)
    assert [option.label for option in plan.candidates] == [
        "Camera capture (EXIF)",
        "Filename",
        "File modified",
    ]
    assert plan.earliest.capture
    assert not plan.candidates[2].capture
    assert plan.proposed_name(plan.earliest) == "My_2023-02-01-000000-01.Photo.JPG"
    assert plan.proposed_name(plan.earliest, use_prefix=False) == plan.proposed_name(plan.earliest)


def test_reconcile_new_stamp_uses_placement():
    facts = FileFacts(candidates=[fact("mtime", "2024-02-01T00:00:00")])
    plan = reconcile_dates("My.Photo.JPG", facts, UTC)
    assert plan.recommended is plan.earliest
    assert plan.proposed_name(plan.earliest) == "2024-02-01-000000-My.Photo.JPG"
    assert plan.proposed_name(plan.earliest, use_prefix=False) == "My.Photo-2024-02-01-000000.JPG"


def test_partial_date_compares_whole_day_and_uses_noon():
    facts = FileFacts(candidates=[fact("mtime", "2024-02-02T00:00:00")])
    plan = reconcile_dates("Trip February 1, 2024.JPG", facts, UTC)

    assert plan.recommended is None
    assert not plan.filename.capture
    assert plan.filename.precision == "date"
    assert plan.filename.warning
    assert plan.filename.moment.hour == 12
    assert plan.proposed_name(plan.filename) == plan.name

    same_day = FileFacts(candidates=[fact("mtime", "2024-02-01T09:00:00")])
    assert reconcile_dates(plan.name, same_day, UTC).recommended.label == "File modified"


def test_partial_filename_prefers_complete_same_day_and_preserves_suffix():
    facts = FileFacts(candidates=[fact("EXIF", "2015-12-29T20:43:24", capture=True)])
    plan = reconcile_dates("2015-12-29-10.jxl", facts, UTC)
    assert plan.recommended is plan.earliest
    assert plan.proposed_name(plan.recommended) == "2015-12-29-204324-10.jxl"


def test_partial_filename_and_later_complete_date_needs_review():
    facts = FileFacts(candidates=[fact("EXIF", "2015-12-30T20:43:24", capture=True)])
    plan = reconcile_dates("2015-12-29-10.jxl", facts, UTC)
    assert plan.recommended is None


def test_partial_filename_and_earlier_complete_date_uses_earlier():
    facts = FileFacts(candidates=[fact("EXIF", "2015-12-28T20:43:24", capture=True)])
    plan = reconcile_dates("2015-12-29-10.jxl", facts, UTC)
    assert plan.recommended is plan.earliest


@pytest.mark.parametrize("clock", ["09:00:00", "23:59:59"])
def test_partial_day_matches_complete_clock(clock):
    facts = FileFacts(candidates=[fact("EXIF", f"2015-12-29T{clock}", capture=True)])
    assert reconcile_dates("2015-12-29-1.jpg", facts, UTC).recommended.precision == "second"


def test_minute_precision_uses_matching_second():
    facts = FileFacts(candidates=[fact("EXIF", "2024-02-01T12:00:34", capture=True)])
    plan = reconcile_dates("2024-02-01-1200-trip.jpg", facts, UTC)
    assert plan.filename.precision == "minute"
    assert plan.recommended is plan.earliest
    assert plan.proposed_name(plan.recommended) == "2024-02-01-120034-trip.jpg"


def test_exact_filename_wins_tie_and_can_be_reviewed_for_capture():
    facts = FileFacts(candidates=[fact("mtime", "2024-02-01T00:00:00")])
    plan = reconcile_dates("Trip 20240201T000000.JPG", facts, UTC)
    assert plan.recommended is plan.filename
    assert plan.filename.capture


@pytest.mark.parametrize(
    ("stamp", "warning"),
    [
        ("2024-11-03-013000", "occurs twice"),
        ("2024-03-10-023000", "does not exist"),
    ],
)
def test_local_fold_or_gap_blocks_filename_and_capture(stamp, warning):
    zone = ZoneInfo("America/New_York")
    plan = reconcile_dates(f"{stamp}-Trip.JPG", FileFacts(), zone)

    assert plan.recommended is None
    assert plan.filename.blocked
    assert warning in plan.filename.warning
    assert not plan.filename.capture


def test_embedded_instant_whose_rendered_name_is_ambiguous_is_blocked():
    zone = ZoneInfo("America/New_York")
    facts = FileFacts(candidates=[fact("EXIF", "2024-11-03T06:30:00", capture=True)])
    plan = reconcile_dates("Trip.JPG", facts, zone)

    assert plan.recommended is None
    assert plan.earliest.blocked
    assert plan.proposed_name(plan.earliest) == "Trip.JPG"


def test_estimated_filename_cannot_become_confirmed_capture():
    name = "2015-07-01-100010-estimated-IMG_1042.jpg"
    plan = reconcile_dates(name, FileFacts(), UTC)
    assert plan.filename and plan.filename.inferred and not plan.filename.capture
    assert plan.proposed_name(plan.filename) == name
