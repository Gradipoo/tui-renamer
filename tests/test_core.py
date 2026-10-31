"""Behavior tests for rename rules, filename dates, sidecars, and zones."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tui_renamer.core import (
    FileFacts,
    RenameRules,
    active_steps,
    conflict_destination,
    datetime_for_write,
    filter_timezones,
    format_offset,
    json_candidates,
    list_files,
    list_timezones,
    load_facts,
    mtime_source,
    naive_to_ts,
    name_diff_ops,
    parse_filename_datetime,
    parse_json_geodata,
    parse_json_time_field,
    plan_filename_dates,
    preview_name,
    rename_paths,
    reset_name_rules,
    resolve_tz,
    show_not_oldest_warning,
    strip_existing_timestamps,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def test_step_order_and_prefix_label() -> None:
    rules = RenameRules(
        use_json_data=True,
        source_tz="UTC",
        strip_to_text=True,
        strip_original_name=False,
        remove_caps=True,
        remove_special=True,
        replace_dots=True,
        replace_spaces=True,
        add_datetime=True,
        add_modtime=True,
        add_origtime=True,
        use_prefix=True,
        add_geodata=True,
    )
    steps = active_steps(rules)
    assert [step.key for step in steps] == [
        "e",
        "T",
        "y",
        "c",
        "s",
        ".",
        "p",
        "t",
        "d",
        "m",
        "o",
        "b",
    ]
    assert next(step.label for step in steps if step.key == "t") == "Prefix"
    rules.use_prefix = False
    assert next(step.label for step in active_steps(rules) if step.key == "t") == "Suffix"
    rules.strip_original_name = True
    keys = [step.key for step in active_steps(rules)]
    assert "x" in keys
    assert "y" not in keys


def test_inactive_steps_are_hidden() -> None:
    assert active_steps(RenameRules()) == []
    steps = active_steps(RenameRules(add_geodata=True))
    assert [step.key for step in steps] == ["b"]


def test_reset_keeps_the_zone() -> None:
    rules = RenameRules(add_datetime=True, source_tz="UTC", use_prefix=False)
    reset_name_rules(rules)
    assert rules.add_datetime is False
    assert rules.use_prefix is True
    assert rules.source_tz == "UTC"


def test_timestamp_strip_always_runs() -> None:
    facts = FileFacts()
    rules = RenameRules()
    assert preview_name("2020-01-02-030405-photo.jpg", facts, rules) == "photo.jpg"
    assert preview_name("photo-2020-01-02-030405.jpg", facts, rules) == "photo.jpg"
    assert preview_name("2020-01-02-030405.jpg", facts, rules) == "2020-01-02-030405.jpg"
    assert strip_existing_timestamps("IMG_20200102_030405") == "IMG_20200102_030405"


def test_prefix_suffix_and_geodata_placement() -> None:
    facts = FileFacts(origtime_text="2020-01-01-000000", geodata="geo-1.00n-2.00e")
    rules = RenameRules(add_origtime=True, add_geodata=True, use_prefix=True)
    assert preview_name("photo.jpg", facts, rules) == "2020-01-01-000000-geo-1.00n-2.00e-photo.jpg"
    rules.use_prefix = False
    assert preview_name("photo.jpg", facts, rules) == "geo-1.00n-2.00e-photo-2020-01-01-000000.jpg"


def test_clean_order_and_letter_strip() -> None:
    facts = FileFacts()
    rules = RenameRules(
        remove_caps=True, remove_special=True, replace_dots=True, replace_spaces=True
    )
    assert preview_name("My File.Name!.jpg", facts, rules) == "my-file-name.jpg"
    text_rules = RenameRules(strip_to_text=True)
    assert preview_name("My File 2020!.jpg", facts, text_rules) == "My File .jpg"
    text_rules.strip_original_name = True
    assert preview_name("My File 2020!.jpg", facts, text_rules) == "My File 2020!.jpg"


def test_today_stamp_and_double_dash_collapse() -> None:
    facts = FileFacts()
    rules = RenameRules(
        add_datetime=True,
        datetime_stamp="2020-01-02-030405",
        strip_original_name=True,
    )
    assert preview_name("photo.jpg", facts, rules) == "2020-01-02-030405.jpg"
    rules.datetime_stamp = "2020-01-02-030405"
    rules.strip_original_name = False
    rules.add_modtime = True
    facts.modtime_text = ""
    assert preview_name("photo.jpg", facts, rules) == "2020-01-02-030405-photo.jpg"


def test_not_oldest_warning() -> None:
    older = datetime(2020, 1, 1, tzinfo=UTC)
    newer = datetime(2021, 1, 1, tzinfo=UTC)
    facts = FileFacts(origtime=older, modtime=newer)
    assert show_not_oldest_warning(facts, RenameRules(add_modtime=True)) is True
    assert show_not_oldest_warning(facts, RenameRules(add_origtime=True)) is False
    assert show_not_oldest_warning(facts, RenameRules(add_modtime=True, add_origtime=True)) is False


def test_mtime_source_uses_the_system_zone_for_today() -> None:
    local = timezone(timedelta(hours=-8))
    facts = FileFacts(
        origtime=datetime(2020, 1, 1, tzinfo=UTC),
        origtime_text="2019-12-31-160000",
        modtime=datetime(2021, 1, 1, tzinfo=UTC),
        modtime_text="2020-12-31-160000",
    )
    rules = RenameRules(add_datetime=True, datetime_stamp="2020-05-01-120000")
    label, moment = mtime_source(facts, rules, local)
    assert label == "2020-05-01-120000"
    assert moment is not None
    assert moment.tzinfo is local
    assert moment.hour == 12
    rules.add_datetime = False
    rules.add_modtime = True
    assert mtime_source(facts, rules, local)[0] == "2020-12-31-160000"
    rules.add_modtime = False
    assert mtime_source(facts, RenameRules(), local)[0] == "2019-12-31-160000"
    assert mtime_source(FileFacts(), RenameRules(), local) == ("N/A", None)
    bad = RenameRules(add_datetime=True, datetime_stamp="nope")
    assert mtime_source(facts, bad, local) == ("Invalid Date", None)


def test_name_diff_marks_insert_and_delete() -> None:
    ops = name_diff_ops("photo.jpg", "2020-photo.jpg")
    assert ("insert", "2020-") in ops
    assert ("equal", "photo.jpg") in ops
    assert name_diff_ops("same.txt", "same.txt") == [("equal", "same.txt")]


def test_filename_date_patterns() -> None:
    cases = {
        "2024-01-01-123456.jpg": (datetime(2024, 1, 1, 12, 34, 56), "second"),
        "IMG_20240101_123456.jpg": (datetime(2024, 1, 1, 12, 34, 56), "second"),
        "2024-01-01 12.34.56.jpg": (datetime(2024, 1, 1, 12, 34, 56), "second"),
        "2024-01-01_1234.jpg": (datetime(2024, 1, 1, 12, 34), "minute"),
        "20240131.jpg": (datetime(2024, 1, 31), "date"),
        "2024-02-31-ok-2024-03-01.jpg": (datetime(2024, 3, 1), "date"),
        "120240101.jpg": (None, None),
        "202401011.jpg": (None, None),
    }
    for name, expected in cases.items():
        assert parse_filename_datetime(name) == expected


def test_filename_date_plan_noon_keep_and_nudge(tmp_path: Path) -> None:
    keep = tmp_path / "20240131-keep.txt"
    noon = tmp_path / "20240115-noon.txt"
    first = tmp_path / "a-20240101-120000.txt"
    second = tmp_path / "b-20240101-120000.txt"
    for path in (keep, noon, first, second):
        path.write_text("x", encoding="utf-8")
    keep_ts = datetime(2024, 1, 31, 8, 15, tzinfo=UTC).timestamp()
    os.utime(keep, (keep_ts, keep_ts))
    other = datetime(2024, 2, 1, 0, 0, tzinfo=UTC).timestamp()
    os.utime(noon, (other, other))
    os.utime(first, (other, other))
    os.utime(second, (other, other))

    plan = plan_filename_dates([keep, noon, first, second], UTC)
    kept = next(item for item in plan.same if item.path == keep)
    assert kept.prec == "kept"
    assert kept.target == keep_ts
    assert keep.name in [] or kept.path.name not in plan.skipped

    noon_item = next(item for item in plan.todo if item.path == noon)
    expected_noon = datetime(2024, 1, 15, 12, tzinfo=UTC).timestamp()
    assert noon_item.target == expected_noon
    assert noon_item.nudged is False

    nudged = [item for item in plan.todo if item.path in (first, second)]
    nudged.sort(key=lambda item: item.target)
    assert len(nudged) == 2
    assert nudged[1].target == nudged[0].target + 1
    assert nudged[1].nudged is True


def test_filename_date_noon_uses_the_selected_zone(tmp_path: Path) -> None:
    path = tmp_path / "20240115.txt"
    path.write_text("x", encoding="utf-8")
    os.utime(path, (datetime(2020, 1, 1, tzinfo=UTC).timestamp(),) * 2)
    plan = plan_filename_dates([path], NY)
    expected = datetime(2024, 1, 15, 12, tzinfo=NY).timestamp()
    assert plan.todo[0].target == expected


def test_naive_to_ts_local_versus_zone() -> None:
    moment = datetime(2024, 1, 15, 12, 0, 0)
    assert naive_to_ts(moment, UTC) == moment.replace(tzinfo=UTC).timestamp()
    assert naive_to_ts(moment, None) == moment.timestamp()


def test_subsecond_mtime_within_one_second_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "2024-01-31-142500.txt"
    path.write_text("x", encoding="utf-8")
    stamp = datetime(2024, 1, 31, 14, 25, tzinfo=UTC).timestamp() + 0.4
    os.utime(path, (stamp, stamp))
    plan = plan_filename_dates([path], UTC)
    assert plan.todo == []
    assert plan.same[0].path == path


def test_already_matching_filename_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "2024-01-31-142500.txt"
    path.write_text("x", encoding="utf-8")
    stamp = datetime(2024, 1, 31, 14, 25, tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))
    plan = plan_filename_dates([path], UTC)
    assert plan.todo == []
    assert plan.same[0].path == path


def test_missing_file_is_skipped(tmp_path: Path) -> None:
    missing = tmp_path / "20240101-gone.txt"
    plan = plan_filename_dates([missing], UTC)
    assert plan.skipped == ["20240101-gone.txt"]
    assert plan.todo == []


def test_conflict_suffix_and_batch_order(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    outcome = rename_paths([(first, "same.txt"), (second, "same.txt")])
    assert (tmp_path / "same.txt").read_text(encoding="utf-8") == "a"
    assert (tmp_path / "same-01.txt").read_text(encoding="utf-8") == "b"
    assert outcome.conflicts == 1
    assert [dst.name for _src, dst in outcome.done] == ["same.txt", "same-01.txt"]


def test_rename_chain_uses_the_name_that_was_free(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    outcome = rename_paths([(first, "b.txt"), (second, "c.txt")])
    assert (tmp_path / "b-01.txt").read_text(encoding="utf-8") == "a"
    assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "b"
    assert outcome.conflicts == 1


def test_conflict_destination_increments(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    (tmp_path / "photo.txt").write_text("x", encoding="utf-8")
    (tmp_path / "photo-01.txt").write_text("y", encoding="utf-8")
    assert conflict_destination(source, "photo.txt").name == "photo-02.txt"
    assert conflict_destination(source, "free.txt").name == "free.txt"


def test_list_files_skips_hidden_json_and_directories(tmp_path: Path) -> None:
    (tmp_path / "B.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / ".secret").write_text("s", encoding="utf-8")
    (tmp_path / "note.json").write_text("{}", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    assert [path.name for path in list_files(tmp_path)] == ["a.txt", "B.txt"]
    assert list_files(tmp_path / "missing") == []


def test_json_candidate_order(tmp_path: Path) -> None:
    path = tmp_path / "Photo.JPG"
    names = [item.name for item in json_candidates(path)]
    assert names[0] == "Photo.JPG.json"
    assert "Photo.json" in names
    assert "Photo.jpg.json" in names
    assert "photo.jpg.json" in names
    numbered = tmp_path / "photo(1).jpg"
    numbered_names = [item.name for item in json_candidates(numbered)]
    assert "photo.jpg.json" in numbered_names
    stamped = tmp_path / "2020-01-02-030405-photo.jpg"
    assert "photo.jpg.json" in [item.name for item in json_candidates(stamped)]


def test_takeout_time_and_geodata() -> None:
    parsed = parse_json_time_field({"timestamp": "1600000000"})
    assert parsed == datetime.fromtimestamp(1600000000, tz=UTC)
    formatted = parse_json_time_field({"formatted": "Sep 9, 2020, 10:30:00 PM UTC"})
    assert formatted == datetime(2020, 9, 9, 22, 30, tzinfo=UTC)
    assert parse_json_time_field({"formatted": "not a date"}) is None
    assert parse_json_time_field("nope") is None

    label, raw = parse_json_geodata({"geoData": {"latitude": 1.2, "longitude": -3.4}})
    assert label == "geo-1.20n-3.40w"
    assert raw == {"latitude": 1.2, "longitude": -3.4}
    label, raw = parse_json_geodata(
        {
            "geoData": {"latitude": 0.0, "longitude": 9.0},
            "geoDataExif": {"latitude": 4.5, "longitude": 6.25},
        }
    )
    assert label == "geo-4.50n-6.25e"
    assert parse_json_geodata({"geoData": {"latitude": 0.0, "longitude": 0.0}}) == (None, None)
    zero_lat = parse_json_geodata({"geoData": {"latitude": 0.0, "longitude": 10.0}})
    assert zero_lat[0] == "geo-0.00n-10.00e"


def test_load_facts_winner_and_json_error(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"not really a jpeg")
    exif_naive = datetime(2020, 6, 1, 15, 0, 0)
    offset = timezone(timedelta(hours=5, minutes=30))
    facts = load_facts(
        path,
        RenameRules(),
        UTC,
        exif_naive=exif_naive,
        exif_offset=offset,
        exif_offset_text="+05:30",
    )
    assert facts.source == "file"
    assert facts.origtime_text == "2020-06-01-093000"
    assert any(item.label.startswith("EXIF") for item in facts.candidates)
    assert any(item.label == "birth" for item in facts.candidates)

    sidecar = tmp_path / "photo.jpg.json"
    sidecar.write_text(
        '{"photoTakenTime": {"timestamp": "1600000000"},'
        ' "modificationTime": {"timestamp": "1600001000"},'
        ' "geoData": {"latitude": 1, "longitude": 2}}',
        encoding="utf-8",
    )
    rules = RenameRules(use_json_data=True)
    facts = load_facts(path, rules, UTC, exif_naive=exif_naive, exif_offset=offset)
    assert facts.source == "json"
    assert facts.sidecar == sidecar
    assert facts.origtime == datetime.fromtimestamp(1600000000, tz=UTC)
    assert facts.geodata == "geo-1.00n-2.00e"
    assert facts.origtime_text != "2020-06-01-093000"

    sidecar.write_text("{", encoding="utf-8")
    broken = load_facts(path, rules, UTC, exif_naive=exif_naive, exif_offset=offset)
    assert broken.json_error is True
    assert broken.origtime is None
    assert broken.modtime is None
    assert all(not item.label.startswith("EXIF") for item in broken.candidates)


def test_json_error_hides_exif_on_a_dated_name(tmp_path: Path) -> None:
    path = tmp_path / "20240131-photo.jpg"
    path.write_bytes(b"x")
    (tmp_path / "20240131-photo.jpg.json").write_text("{", encoding="utf-8")
    facts = load_facts(
        path,
        RenameRules(use_json_data=True),
        UTC,
        exif_naive=datetime(2020, 1, 1, 0, 0, 0),
    )
    assert [item.label for item in facts.candidates] == ["Filename"]
    assert facts.json_error is True


def test_missing_offset_uses_the_selected_zone(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"x")
    naive = datetime(2024, 1, 15, 12, 0, 0)
    facts = load_facts(path, RenameRules(), NY, exif_naive=naive, exif_offset=None)
    assert facts.origtime is not None
    assert facts.origtime.tzinfo is not None
    assert facts.origtime.astimezone(NY).hour == 12


def test_datetime_for_write_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "2024-01-31-142500.txt"
    path.write_text("x", encoding="utf-8")
    empty = FileFacts()
    chosen = datetime_for_write(path, empty, UTC)
    assert chosen == datetime(2024, 1, 31, 14, 25, tzinfo=UTC)
    stamped = FileFacts(origtime=datetime(1999, 1, 1, tzinfo=UTC))
    assert datetime_for_write(path, stamped, UTC).year == 1999
    plain = tmp_path / "plain.txt"
    plain.write_text("x", encoding="utf-8")
    assert datetime_for_write(plain, FileFacts(), UTC) is not None


def test_timezone_list_and_filter() -> None:
    names = list_timezones()
    assert names[0] == "Local System"
    assert names[1] == "UTC"
    assert all(not name.startswith(("Etc/", "posix/", "right/", "SystemV/")) for name in names)
    sample = ["Local System", "UTC", "America/Los_Angeles", "America/New_York", "Europe/Paris"]
    assert filter_timezones(sample, "") == sample
    assert filter_timezones(sample, "los angeles") == ["America/Los_Angeles"]
    assert filter_timezones(sample, "gmt") == ["UTC"]
    assert filter_timezones(sample, "local") == ["Local System"]
    assert filter_timezones(sample, "america york") == ["America/New_York"]
    assert filter_timezones(sample, "nope") == []


def test_resolve_and_format_offset() -> None:
    assert resolve_tz("UTC") is UTC
    assert resolve_tz(None).utcoffset(datetime.now()) is not None
    moment = datetime(2024, 1, 15, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert format_offset(moment) == "+05:30"
    negative = datetime(2024, 1, 15, 12, tzinfo=timezone(timedelta(hours=-8)))
    assert format_offset(negative) == "-08:00"
