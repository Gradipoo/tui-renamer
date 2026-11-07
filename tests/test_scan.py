"""Scan pairing, dates, notes sidecars, and mock Ollama parsing."""

from __future__ import annotations

import json
import urllib.error
from datetime import timezone
from pathlib import Path

import pytest
from PIL import Image

from tui_renamer.core import RenameRules, json_candidates, load_facts, rename_paths
from tui_renamer.scan_backend import (
    BackendError,
    MockBackend,
    OllamaBackend,
    parse_ollama_body,
    parse_structured,
    parse_tags,
    show_has_vision,
)
from tui_renamer.scan_bits import (
    Bit,
    apply_scan_suffix,
    capture_from_bits,
    description_from_bits,
    format_scan_part,
    format_scan_parts,
    join_scan_name,
    selected_labels,
    strip_accepted_suffix,
)
from tui_renamer.scan_candidates import FileClocks, aware_delta, score_candidates
from tui_renamer.scan_config import host_is_local
from tui_renamer.scan_dates import ScanDate, can_queue_capture, parse_date_text, parse_model_dates
from tui_renamer.scan_models import (
    DEFAULT,
    LIGHT,
    pull_command,
    recs_for_ram,
    setup_lines,
    suggested_rec,
)
from tui_renamer.scan_notes import (
    ScanNotes,
    is_notes_sidecar,
    load_notes,
    notes_path_for,
    notes_renames,
    rewrite_pairing_names,
    save_notes,
    takeout_sidecar,
)
from tui_renamer.scan_pairing import sizes_similar, suggest_pairs

UTC = timezone.utc


def _jpeg(
    path: Path,
    size: tuple[int, int] = (20, 12),
    color: tuple[int, int, int] = (40, 40, 40),
) -> None:
    Image.new("RGB", size, color).save(path, format="JPEG")


def test_filename_a_b_pair(tmp_path: Path) -> None:
    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    _jpeg(front)
    _jpeg(back)
    records = suggest_pairs([front, back])
    assert len(records) == 1
    assert records[0].front == front
    assert records[0].back == back
    assert records[0].reason == "filename"
    assert records[0].confirmed is False


def test_back_suffix_and_neighbor(tmp_path: Path) -> None:
    one = tmp_path / "scan-001.jpg"
    two = tmp_path / "scan-002.jpg"
    tagged = tmp_path / "letter-back.jpg"
    _jpeg(one)
    _jpeg(two)
    _jpeg(tagged)
    records = suggest_pairs([one, tagged, two])
    named = [item for item in records if item.reason == "filename"]
    assert named == []
    neighbor = [item for item in records if item.reason == "neighbor"]
    assert neighbor
    assert neighbor[0].confirmed is False


def test_filename_ambiguity_is_not_confirmed(tmp_path: Path) -> None:
    front = tmp_path / "shot_a.jpg"
    back_b = tmp_path / "shot_b.jpg"
    back_back = tmp_path / "shot_back.jpg"
    _jpeg(front)
    _jpeg(back_b)
    _jpeg(back_back)
    records = suggest_pairs([front, back_b, back_back])
    match = next(item for item in records if item.front == front)
    assert match.ambiguous is True
    assert match.back is None
    assert back_b in match.others
    assert back_back in match.others
    assert match.confirmed is False


def test_size_is_not_confirmation(tmp_path: Path) -> None:
    front = tmp_path / "a.jpg"
    close = tmp_path / "b.jpg"
    _jpeg(front, (40, 30))
    _jpeg(close, (41, 30))
    assert sizes_similar(front, close) is True
    records = suggest_pairs([front, close])
    assert records[0].size_similar is True
    assert records[0].confirmed is False
    assert records[0].reason == "neighbor"


def test_year_stays_a_year() -> None:
    dates = parse_date_text("1952")
    assert len(dates) == 1
    assert dates[0].kind == "year"
    assert dates[0].year == 1952
    assert dates[0].month is None
    assert dates[0].day is None
    model = parse_model_dates(
        [{"kind": "day", "text": "1952", "year": 1952, "month": None, "day": 1}]
    )
    assert model == []
    alt = parse_date_text("1952 or 1953")
    assert {item.year for item in alt} == {1952, 1953}
    assert all(item.uncertain for item in alt)


def test_season_and_day_parse() -> None:
    season = parse_date_text("summer 1961")
    assert season[0].kind == "season"
    assert season[0].season == "summer"
    assert season[0].year == 1961
    day = parse_date_text("1961-07-04")
    assert day[0].kind == "day"
    assert day[0].day == 4


def test_notes_sidecar_is_not_a_takeout_date(tmp_path: Path) -> None:
    photo = tmp_path / "Photo.JPG"
    photo.write_bytes(b"jpeg")
    takeout = tmp_path / "photo.jpg.json"
    takeout.write_text(
        json.dumps({"photoTakenTime": {"timestamp": "1600000000"}}),
        encoding="utf-8",
    )
    notes = ScanNotes(
        front=photo.name,
        back="Photo_b.JPG",
        pairing_reason="manual",
        pairing_confirmed=True,
        engine="mock",
        model="mock",
        raw_transcription="Aunt May 1952",
        notes="keep",
        dates=parse_date_text("1952"),
        accepted_at="2026-01-01T00:00:00Z",
    )
    save_notes(notes_path_for(photo), notes)
    assert notes_path_for(photo).name.endswith(".rn-notes.json")
    assert notes_path_for(photo).name not in [item.name for item in json_candidates(photo)]
    sidecar = takeout_sidecar(photo, json_candidates(photo))
    assert sidecar is not None
    assert sidecar.samefile(takeout)
    assert not is_notes_sidecar(sidecar)
    facts = load_facts(photo, RenameRules(use_json_data=True), UTC)
    assert facts.winner_source == "J"
    loaded = load_notes(notes_path_for(photo))
    assert loaded is not None
    assert loaded.engine == "mock"
    assert loaded.dates[0].kind == "year"


def test_misnamed_notes_json_is_skipped(tmp_path: Path) -> None:
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"jpeg")
    fake = tmp_path / "pic.jpg.json"
    fake.write_text(
        json.dumps(
            {
                "format": "rn-notes",
                "version": 1,
                "front": "pic.jpg",
                "notes": "not takeout",
                "dates": [{"kind": "year", "text": "1952", "year": 1952}],
                "recognition": {"engine": "mock", "model": "", "raw_transcription": "1952"},
                "pairing": {"reason": "none", "confirmed": True},
                "accepted_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert is_notes_sidecar(fake)
    facts = load_facts(photo, RenameRules(use_json_data=True), UTC)
    assert facts.winner_source != "J"
    assert facts.sidecar is None


def test_notes_follow_a_rename(tmp_path: Path) -> None:
    photo = tmp_path / "old.jpg"
    _jpeg(photo)
    save_notes(
        notes_path_for(photo),
        ScanNotes(
            front="old.jpg",
            back="old_b.jpg",
            pairing_reason="filename",
            pairing_confirmed=True,
            engine="manual",
            model="",
            raw_transcription="hi",
            notes="",
            accepted_at="2026-01-01T00:00:00Z",
        ),
    )
    outcome = rename_paths([(photo, "new.jpg")])
    extra = notes_renames(outcome.done)
    extra_out = rename_paths(extra)
    name_map = {src.name: dst.name for src, dst in outcome.done}
    rewrite_pairing_names(extra_out.done[0][1], name_map)
    assert (tmp_path / "new.jpg").exists()
    sidecar = notes_path_for(tmp_path / "new.jpg")
    assert sidecar.exists()
    loaded = load_notes(sidecar)
    assert loaded is not None
    assert loaded.front == "new.jpg"
    assert loaded.back == "old_b.jpg"


def test_mock_backend_and_malformed_ollama() -> None:
    mock = MockBackend()
    result = mock.recognize(Path("unused.jpg"), model="mock")
    assert result.engine == "mock"
    assert result.dates[0].month == 6
    failing = MockBackend(error="nope")
    with pytest.raises(BackendError, match="nope"):
        failing.recognize(Path("unused.jpg"), model="x")
    with pytest.raises(BackendError):
        parse_ollama_body(b"not json", model="llava")
    with pytest.raises(BackendError, match="structured"):
        parse_ollama_body(b'{"response": "hello"}', model="llava")
    with pytest.raises(BackendError, match="transcription"):
        parse_structured({"dates": []}, engine="ollama", model="x", raw="{}")
    body = json.dumps(
        {
            "response": json.dumps(
                {
                    "transcription": "May 1952",
                    "notes": "",
                    "dates": [{"kind": "year", "text": "1952", "year": 1952}],
                }
            )
        }
    ).encode()
    parsed = parse_ollama_body(body, model="llava")
    assert parsed.transcription == "May 1952"
    assert parsed.dates[0].kind == "year"
    assert parsed.dates[0].day is None
    ignored = parse_structured(
        {
            "transcription": "ok",
            "dates": [],
            "path": "/etc/passwd",
            "command": "rm -rf /",
        },
        engine="ollama",
        model="x",
        raw="{}",
    )
    assert ignored.transcription == "ok"
    with pytest.raises(BackendError, match="too large"):
        parse_ollama_body(b"x" * (1024 * 1024 + 2), model="llava")


def test_ollama_timeout_and_http_error(tmp_path: Path) -> None:
    path = tmp_path / "back.jpg"
    _jpeg(path)

    def boom(_url: str, _payload: dict, *, timeout: int) -> bytes:
        del timeout
        raise TimeoutError("late")

    backend = OllamaBackend(poster=boom)
    with pytest.raises(BackendError, match="timed out"):
        backend.recognize(path, model="llava")

    def refuse(_url: str, _payload: dict, *, timeout: int) -> bytes:
        del timeout
        raise ConnectionRefusedError("nope")

    backend = OllamaBackend(poster=refuse)
    with pytest.raises(BackendError, match="not reachable"):
        backend.recognize(path, model="llava")

    empty = OllamaBackend()
    with pytest.raises(BackendError, match="No model"):
        empty.recognize(path, model="  ")

    def missing(_url: str, _payload: dict, *, timeout: int) -> bytes:
        del timeout
        raise urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/generate",
            404,
            "not found",
            None,
            None,
        )

    backend = OllamaBackend(poster=missing)
    with pytest.raises(BackendError, match="ollama pull"):
        backend.recognize(path, model="missing-vision")


def test_tags_and_vision_filter() -> None:
    names = parse_tags(
        json.dumps(
            {
                "models": [
                    {"name": "gemma3:4b"},
                    {"name": "gemma3:1b"},
                    {"name": "gemma3:4b"},
                    {"size": 1},
                ]
            }
        ).encode()
    )
    assert names == ["gemma3:4b", "gemma3:1b"]
    with pytest.raises(BackendError, match="not JSON"):
        parse_tags(b"nope")
    with pytest.raises(BackendError, match="missing models"):
        parse_tags(b"{}")
    assert show_has_vision(b'{"capabilities": ["completion", "vision"]}') is True
    assert show_has_vision(b'{"capabilities": ["completion"]}') is False
    assert show_has_vision(b'{"model_info": {"gemma3.vision.block_count": 16}}') is True
    with pytest.raises(BackendError, match="not JSON"):
        show_has_vision(b"{")


def test_list_vision_models_local_only_and_timeout() -> None:
    shown: list[str] = []

    def getter(_url: str, *, timeout: int) -> bytes:
        del timeout
        return json.dumps({"models": [{"name": "gemma3:4b"}, {"name": "gemma3:1b"}]}).encode()

    def poster(url: str, payload: dict, *, timeout: int) -> bytes:
        del timeout
        shown.append(str(payload.get("model")))
        if "generate" in url:
            raise AssertionError("discovery must not generate")
        name = payload.get("model")
        if name == "gemma3:4b":
            return json.dumps({"capabilities": ["completion", "vision"]}).encode()
        return json.dumps({"capabilities": ["completion"]}).encode()

    local = OllamaBackend(poster=poster, getter=getter)
    assert local.list_vision_models() == ["gemma3:4b"]
    assert shown == ["gemma3:4b", "gemma3:1b"]

    remote = OllamaBackend(
        host="http://example.test:11434",
        poster=poster,
        getter=getter,
    )
    assert remote.list_vision_models() == []

    def late(_url: str, *, timeout: int) -> bytes:
        del timeout
        raise TimeoutError("slow")

    timed = OllamaBackend(getter=late, poster=poster)
    with pytest.raises(BackendError, match="timed out"):
        timed.list_vision_models()

    def down(_url: str, *, timeout: int) -> bytes:
        del timeout
        raise ConnectionRefusedError("nope")

    missing = OllamaBackend(getter=down, poster=poster)
    with pytest.raises(BackendError, match="ollama serve"):
        missing.list_vision_models()


def test_recommendations_and_setup_text() -> None:
    assert LIGHT.tag == "qwen3.5:2b"
    assert DEFAULT.tag == "gemma3:4b"
    assert "gemma3:270m" not in {item.tag for item in recs_for_ram(None)}
    assert suggested_rec(2 * 1024**3).tag == LIGHT.tag
    assert suggested_rec(16 * 1024**3).tag == DEFAULT.tag
    assert suggested_rec(None, tier="light").tag == LIGHT.tag
    assert pull_command("gemma3:4b") == "ollama pull gemma3:4b"
    assert "rm" not in pull_command("gemma3:4b; rm -rf /")
    assert host_is_local("http://127.0.0.1:11434")
    assert not host_is_local("http://example.test:11434")
    lines = setup_lines(
        host="http://example.test:11434",
        local_host=False,
        total_bytes=None,
        installed=[],
        discover_error="",
    )
    blob = " ".join(lines)
    assert "optional" in blob.casefold()
    assert "j/k" in blob
    assert "example.test" in blob
    assert LIGHT.pull_command() in blob
    assert DEFAULT.pull_command() in blob
    body = json.dumps({"error": "this model does not support images"}).encode()
    with pytest.raises(BackendError, match="text-only"):
        parse_ollama_body(body, model="gemma3:1b")


def test_written_day_only_can_become_capture() -> None:
    day = ScanDate("day", "1961-07-04", 1961, 7, 4, origin="written")
    year = ScanDate("year", "1952", 1952, origin="written")
    inferred = ScanDate("day", "1961-07-04", 1961, 7, 4, origin="inferred")
    season = ScanDate("season", "summer 1961", 1961, season="summer", origin="written")
    assert can_queue_capture(day) is True
    assert can_queue_capture(year) is False
    assert can_queue_capture(inferred) is False
    assert can_queue_capture(season) is False
    zone = timezone.utc
    year_bit = Bit(
        kind="date",
        label=year.text,
        source="a.jpg",
        model="mock",
        origin="written",
        use_meta=True,
        date=year,
    )
    inferred_bit = Bit(
        kind="date",
        label=inferred.text,
        source="a.jpg",
        model="mock",
        origin="inferred",
        use_meta=True,
        date=inferred,
    )
    day_bit = Bit(
        kind="date",
        label=day.text,
        source="a.jpg",
        model="mock",
        origin="written",
        use_meta=True,
        date=day,
    )
    assert capture_from_bits([year_bit], zone) == (None, "")
    assert capture_from_bits([inferred_bit], zone) == (None, "")
    moment, text = capture_from_bits([day_bit], zone)
    assert text == "1961-07-04"
    assert moment is not None
    assert moment.hour == 12
    assert moment.minute == 0
    token = Bit(
        kind="token",
        label="picnic",
        source="a.jpg",
        model="mock",
        origin="inferred",
        use_meta=True,
    )
    assert description_from_bits([token]) == "picnic"


def test_related_candidate_tiers(tmp_path: Path) -> None:
    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    other = tmp_path / "letter.jpg"
    _jpeg(front)
    _jpeg(back)
    _jpeg(other, (80, 60), (9, 9, 9))
    rows = score_candidates(front, [front, back, other])
    by_name = {item.path.name: item for item in rows}
    assert by_name["trip_b.jpg"].tier == "likely"
    assert by_name["trip_b.jpg"].label == "Likely"
    assert "filename" in by_name["trip_b.jpg"].reason
    assert by_name["letter.jpg"].tier == "unlikely"
    assert "not disproven" in by_name["letter.jpg"].reason
    only_size = tmp_path / "solo.jpg"
    close = tmp_path / "near.jpg"
    _jpeg(only_size, (40, 30))
    _jpeg(close, (41, 30))
    size_rows = score_candidates(only_size, [only_size, close])
    assert size_rows[0].tier != "likely"
    assert "size" in size_rows[0].reason
    one = tmp_path / "scan-001.jpg"
    two = tmp_path / "scan-002.jpg"
    _jpeg(one)
    _jpeg(two)
    neighbor = score_candidates(one, [one, two])[0]
    assert neighbor.tier == "possible"
    assert neighbor.label == "Possible"


def test_related_ranks_closer_time_before_alphabetical(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    subject = tmp_path / "item-1.jpg"
    later_alpha = tmp_path / "item-0.jpg"
    closer = tmp_path / "item-2.jpg"
    _jpeg(subject)
    _jpeg(later_alpha)
    _jpeg(closer)
    now = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    clocks = {
        subject: FileClocks(mtime=now),
        later_alpha: FileClocks(mtime=now + timedelta(seconds=90)),
        closer: FileClocks(mtime=now + timedelta(seconds=3)),
    }
    rows = score_candidates(subject, [subject, later_alpha, closer], clocks=clocks)
    assert rows[0].path.name == "item-2.jpg"
    assert rows[0].tier == "possible"
    assert "mtime" in rows[0].reason
    assert rows[1].path.name == "item-0.jpg"


def test_related_time_rules_and_pairs_beat_size(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    front = tmp_path / "trip_a.jpg"
    back = tmp_path / "trip_b.jpg"
    bulky = tmp_path / "other.jpg"
    _jpeg(front, (40, 30))
    _jpeg(back, (80, 60), (9, 9, 9))
    _jpeg(bulky, (41, 30), (2, 2, 2))
    now = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    clocks = {
        front: FileClocks(mtime=now),
        back: FileClocks(mtime=now),
        bulky: FileClocks(mtime=now),
    }
    rows = score_candidates(front, [front, back, bulky], clocks=clocks)
    assert rows[0].path.name == "trip_b.jpg"
    assert rows[0].tier == "likely"
    by_name = {item.path.name: item for item in rows}
    assert by_name["other.jpg"].tier != "likely"
    naive = datetime(2020, 1, 1, 12, 0)
    assert aware_delta(now, None) is None
    assert aware_delta(now, naive) is None
    missing = FileClocks()
    empty = score_candidates(
        front,
        [front, bulky],
        clocks={front: missing, bulky: missing},
    )
    assert empty[0].tier != "likely"


def test_bit_name_toggle_does_not_enable_meta() -> None:
    bit = Bit(
        kind="text",
        label="picnic",
        source="a.jpg",
        model="",
        origin="written",
    )
    bit.toggle_name()
    assert bit.use_name is True
    assert bit.use_meta is False
    bit.toggle_name()
    assert bit.use_name is False
    assert bit.use_meta is False
    bit.toggle_meta()
    assert bit.use_meta is True
    assert bit.use_name is False


def test_list_loaded_models_uses_ps() -> None:
    def getter(url: str, *, timeout: int) -> bytes:
        del timeout
        if url.endswith("/api/ps"):
            return json.dumps({"models": [{"name": "gemma3:4b"}]}).encode()
        return json.dumps({"models": [{"name": "gemma3:4b"}]}).encode()

    backend = OllamaBackend(getter=getter, poster=lambda *_a, **_k: b"{}")
    assert backend.list_loaded_models() == ["gemma3:4b"]
    remote = OllamaBackend(
        host="http://example.test:11434",
        getter=getter,
        poster=lambda *_a, **_k: b"{}",
    )
    assert remote.list_loaded_models() == []


def test_scan_suffix_order_strip_and_empty_base() -> None:
    first = Bit(
        kind="text",
        label="beta",
        source="a.jpg",
        model="",
        origin="written",
        use_name=True,
        name_order=2,
    )
    second = Bit(
        kind="text",
        label="alpha",
        source="a.jpg",
        model="",
        origin="written",
        use_name=True,
        name_order=1,
    )
    unused = Bit(kind="text", label="skip", source="a.jpg", model="", origin="written")
    assert selected_labels([first, second, unused]) == ["alpha", "beta"]
    first.label = "beta-edit"
    assert selected_labels([first, second]) == ["alpha", "beta-edit"]
    first.use_name = False
    first.name_order = 0
    first.use_name = True
    first.name_order = 3
    assert selected_labels([first, second]) == ["alpha", "beta-edit"]
    first.label = "Aunt May!"
    assert selected_labels([first, second]) == ["alpha", "Aunt May!"]
    assert strip_accepted_suffix("photo-picnic", ["picnic"]) == "photo"
    assert strip_accepted_suffix("picnic", ["picnic"]) == ""
    assert strip_accepted_suffix("photo-picnic", []) == "photo-picnic"
    assert strip_accepted_suffix("summer-day", ["other"]) == "summer-day"
    assert strip_accepted_suffix("photo-picnic", ["pic"]) == "photo-picnic"
    assert join_scan_name("", ".jpg", ["tag1", "tag2"], fallback="old.jpg") == "tag1-tag2.jpg"
    assert not join_scan_name("", ".jpg", ["tag1"], fallback="old.jpg").startswith("-")
    assert apply_scan_suffix("photo.jpg", ["tag"], stem="") == "tag.jpg"
    assert apply_scan_suffix("photo-picnic.jpg", ["lake"], accepted=["picnic"]) == "photo-lake.jpg"
    assert apply_scan_suffix("photo.jpg", []) == "photo.jpg"


def test_format_scan_parts_follows_rename_rules() -> None:
    raw = ["Aunt May!"]
    assert format_scan_parts(raw, RenameRules()) == ["Aunt May!"]
    assert format_scan_parts(raw, RenameRules(remove_caps=True)) == ["aunt may!"]
    assert format_scan_parts(raw, RenameRules(remove_special=True)) == ["Aunt May"]
    assert format_scan_parts(raw, RenameRules(replace_spaces=True)) == ["Aunt-May!"]
    assert format_scan_parts(["file.name"], RenameRules(replace_dots=True)) == ["file-name"]
    combo = RenameRules(
        remove_caps=True,
        remove_special=True,
        replace_spaces=True,
        replace_dots=True,
    )
    assert format_scan_parts(["Aunt May!"], combo) == ["aunt-may"]
    assert format_scan_parts(["Hello.World!"], combo) == ["hello-world"]
    assert format_scan_parts(["!!!", "ok"], RenameRules(remove_special=True)) == ["ok"]
    assert format_scan_parts(["foo/bar"], RenameRules()) == ["foo bar"]
    assert format_scan_parts(["  ", ""], RenameRules()) == []
    long_label = "n" * 80
    assert format_scan_part(long_label, RenameRules()) == "n" * 40
    joined = join_scan_name(
        "keep",
        ".jpg",
        format_scan_parts(["Aunt May!", "Lake"], combo),
        fallback="keep.jpg",
    )
    assert joined == "keep-aunt-may-lake.jpg"
    stamped = apply_scan_suffix(
        "photo.jpg",
        format_scan_parts(["Aunt May!"], combo),
        stem="2020-01-01-000000-photo",
    )
    assert stamped == "2020-01-01-000000-photo-aunt-may.jpg"


def test_notes_roundtrip_keeps_name_suffix(tmp_path: Path) -> None:
    photo = tmp_path / "keep.jpg"
    _jpeg(photo)
    notes = ScanNotes(
        front=photo.name,
        back=None,
        pairing_reason="none",
        pairing_confirmed=False,
        engine="mock",
        model="mock",
        raw_transcription="",
        notes="",
        name_suffix=["alpha", "beta"],
        bits=[
            Bit(
                kind="text",
                label="alpha",
                source=photo.name,
                model="",
                origin="written",
                use_name=True,
                name_order=1,
                manual=True,
            )
        ],
    )
    save_notes(notes_path_for(photo), notes)
    loaded = load_notes(notes_path_for(photo))
    assert loaded is not None
    assert loaded.name_suffix == ["alpha", "beta"]
    assert loaded.bits[0].name_order == 1
