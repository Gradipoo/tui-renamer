"""Earliest filename staging uses eligible instants without writing files."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import Button

from tui_renamer.app import RenamerApp
from tui_renamer.core import (
    DateCandidate,
    FileFacts,
    RenameRules,
    earliest_date,
    earliest_source,
    load_facts,
    preview_name,
)

UTC = timezone.utc


def clock(year):
    return datetime(year, 6, 14, 9, 32, tzinfo=UTC)


@pytest.mark.parametrize("winner", ["EXIF DateTimeOriginal", "birth", "mtime", "JSON creationTime"])
def test_each_source_can_win(winner):
    candidates = [
        DateCandidate(label, "", clock(2019 if label == winner else 2020))
        for label in ["EXIF DateTimeOriginal", "birth", "mtime", "JSON creationTime"]
    ]
    candidates.append(DateCandidate("Filename", "", clock(2000)))
    facts = FileFacts(candidates=candidates)
    assert earliest_date(facts).label == winner
    rules = RenameRules(use_earliest=True, source_tz="UTC")
    assert preview_name("photo.jpg", facts, rules) == "2019-06-14-093200-photo.jpg"
    rules.use_prefix = False
    assert preview_name("photo.jpg", facts, rules) == "photo-2019-06-14-093200.jpg"


def test_ties_offsets_missing_and_labels():
    instant = clock(2019)
    offset = timezone(timedelta(hours=8))
    a = DateCandidate("mtime", "", instant)
    b = DateCandidate("EXIF DateTimeOriginal", "", instant.astimezone(offset))
    for candidates in ([a, b], [b, a]):
        facts = FileFacts(candidates=candidates)
        assert earliest_date(facts) == b
        assert earliest_source(b) == "Camera capture (EXIF)"
        assert preview_name("a.jpg", facts, RenameRules(use_earliest=True, source_tz="UTC")) == (
            "2019-06-14-093200-a.jpg"
        )
    assert earliest_source(a) == "File modified"
    assert earliest_source(DateCandidate("birth", "")) == "File created"
    facts = FileFacts(candidates=[DateCandidate("Filename", "", clock(2000))])
    assert earliest_date(facts) is None
    assert preview_name("a.jpg", facts, RenameRules(use_earliest=True)) == "a.jpg"


@pytest.mark.parametrize("enabled", [False, True])
def test_sidecars_and_filesystem_candidates(tmp_path, monkeypatch, enabled):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"fixture")
    path.with_name("a.jpg.json").write_text(
        json.dumps({"photoTakenTime": {"timestamp": str(int(clock(2018).timestamp()))}})
    )
    monkeypatch.setattr("tui_renamer.core._fs_times", lambda _: (clock(2021), clock(2020)))
    rules = RenameRules(use_earliest=True, use_json_data=enabled, source_tz="UTC")
    facts = load_facts(path, rules, UTC, datetime(2019, 6, 14, 9, 32))
    assert earliest_date(facts).instant == clock(2018 if enabled else 2019)


async def test_button_shortcut_batch_locks_manual_and_narrow(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (tmp_path / name).write_text(name)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    app = RenamerApp(tmp_path)
    app.rules.source_tz = "UTC"
    async with app.run_test(size=(60, 32)) as pilot:
        await pilot.pause()
        for i, path in enumerate(app.files):
            facts = FileFacts(
                candidates=[DateCandidate("mtime" if i else "EXIF", "", clock(2018 + i))]
            )
            app._facts[(str(path), "UTC", False)] = facts
        app.locked[2] = "locked.txt"
        app.manual[3] = "edited.txt"
        app.refresh_view()
        await pilot.pause()
        button = app.query_one("#opt-use_earliest", Button)
        assert button.region.width >= len("E Use earliest date")
        assert app.screen.region.contains_region(button.region)
        hint = str(app.query_one("#earliest-hint").render())
        assert "2018-06-14 09:32" in hint
        await pilot.click("#opt-use_earliest")
        await pilot.pause()
        clicked = [app.preview_for(i) for i in range(4)]
        assert clicked == [
            "2018-06-14-093200-a.txt",
            "2019-06-14-093200-b.txt",
            "locked.txt",
            "edited.txt",
        ]
        assert "ON" in str(button.label)
        await pilot.press("E")
        await pilot.press("E")
        assert [app.preview_for(i) for i in range(4)] == clicked
        await pilot.press("d", "m", "o")
        assert not app.rules.use_earliest
        assert app.rules.add_datetime and app.rules.add_modtime and app.rules.add_origtime
        assert "Choose ‘Use earliest date’" in app._earliest_hint()
        await pilot.press("E")
        assert not any((app.rules.add_datetime, app.rules.add_modtime, app.rules.add_origtime))
        assert not app.date_mode and not app.mtime_mode
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


def test_offsets_compare_instants_not_local_clock():
    later = DateCandidate("EXIF", "", datetime(2020, 1, 1, 8, tzinfo=UTC))
    earlier = DateCandidate(
        "mtime", "", datetime(2020, 1, 1, 9, tzinfo=timezone(timedelta(hours=2)))
    )
    facts = FileFacts(candidates=[later, earlier])
    assert earliest_date(facts) == earlier
    assert preview_name("a.jpg", facts, RenameRules(use_earliest=True, source_tz="UTC")) == (
        "2020-01-01-070000-a.jpg"
    )


def test_bad_sidecar_does_not_hide_earliest(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"fixture")
    path.with_name("a.jpg.json").write_text("{")
    monkeypatch.setattr("tui_renamer.core._fs_times", lambda _: (clock(2021), None))
    facts = load_facts(path, RenameRules(use_json_data=True), UTC)
    assert facts.json_error
    assert earliest_date(facts).label == "mtime"


async def test_hint_without_detail_and_no_date(tmp_path):
    (tmp_path / "a.txt").write_text("fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(60, 32)) as pilot:
        await pilot.pause()
        path = app.files[0]
        app._facts[(str(path), None, False)] = FileFacts()
        app.refresh_view()
        await pilot.press("1")
        assert not app.query_one("#detail").display
        hint = app.query_one("#earliest-hint")
        assert hint.visible and hint.region.height > 0
        assert "No date available" in str(hint.render())
        await pilot.press("E")
        assert app.preview_for(0) == "a.txt"
