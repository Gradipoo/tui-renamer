"""Action groups, pending scope and recovery feedback use the actual file operations."""

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from textual.widgets import Button, Collapsible, Static

from tui_renamer.app import ConfirmScreen, RenamerApp, ResultScreen
from tui_renamer.core import plan_filename_dates
from tui_renamer.journal import MetaOp
from tui_renamer.scan_bits import PendingScan, ScanSession
from tui_renamer.table_view import TableView


def photo(path: Path) -> None:
    Image.new("RGB", (8, 8), (30, 40, 50)).save(path)


def pending_text(app: RenamerApp) -> str:
    return str(app.query_one("#pending-summary", Static).render())


async def test_pending_scope_distinguishes_preview_selection_and_hidden_staging(
    tmp_path: Path,
) -> None:
    photo(tmp_path / "A.jpg")
    photo(tmp_path / "B.jpg")
    (tmp_path / "C.txt").write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.rules.remove_caps = True
        app.refresh_view()
        assert len(app._changing) == 3
        assert "0 renames" in pending_text(app)
        app.locked[0] = "locked.jpg"
        app.manual[1] = "manual.jpg"
        app.scan_pending[str(app.files[0])] = PendingScan(app.files[0], description="written note")
        app.scan_sessions[str(app.files[0])] = ScanSession()
        app.table_view = TableView(extension="txt")
        app.refresh_view()
        await pilot.pause()
        summary = pending_text(app)
        assert "2 renames" in summary
        assert "1 metadata" in summary
        assert "1 notes" in summary
        assert "Hidden targets: 2" in summary
        app.action_select_all()
        assert "3 renames" in pending_text(app)
        assert app.selected == {2}
        assert {path.name for path, _name in app._rename_pairs()} == {"A.jpg", "B.jpg", "C.txt"}
        assert sorted(path.name for path in tmp_path.iterdir()) == ["A.jpg", "B.jpg", "C.txt"]


async def test_pending_scope_follows_write_modes_and_preserves_hidden_selection(
    tmp_path: Path,
) -> None:
    for name in ("2020-01-01-a.txt", "2020-01-02-b.jpg"):
        (tmp_path / name).write_text("fixture", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.rules.source_tz = "UTC"
        app.manual[0] = "staged.txt"
        app.table_view = TableView(extension="jpg")
        app.action_date_mode()
        await pilot.pause()
        plan = plan_filename_dates(app.files, app.plan_zone())
        assert f"Apply filename dates: {len(plan.todo)} files" in pending_text(app)
        assert "Hidden targets: 1" in pending_text(app)
        assert "work waits for rename mode" in pending_text(app)
        app.selected = {0}
        app.refresh_view()
        assert "Apply filename dates: 1 files" in pending_text(app)
        assert "Selection: 1 (1 hidden)" in pending_text(app)
        app.action_mtime_mode()
        assert "Apply file times: 1 files" in pending_text(app)
        app.action_select_none()
        assert "Apply file times: 0 files" in pending_text(app)
        assert app.manual == {0: "staged.txt"}
        assert (tmp_path / "2020-01-01-a.txt").exists()


async def test_resets_and_discard_have_separate_scopes(tmp_path: Path) -> None:
    source = tmp_path / "original.txt"
    source.write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.rules.remove_caps = True
        app.manual[0] = "manual.txt"
        app.locked[0] = "locked.txt"
        app.selected = {0}
        app.scan_suffix[str(source)] = ["tag"]
        app.scan_pending[str(source)] = PendingScan(source, description="note")
        app.action_reset_rules()
        assert not app.rules.remove_caps
        assert app.manual == {0: "manual.txt"}
        assert app.locked == {0: "locked.txt"}
        assert app.scan_suffix[str(source)] == ["tag"]
        app.action_select_none()
        assert not app.selected
        assert app.manual
        app.action_discard_manual_name()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.manual == {0: "manual.txt"}
        app.action_discard_manual_name()
        await pilot.press("y")
        await pilot.pause()
        assert not app.manual
        assert app.locked == {0: "locked.txt"}
        assert str(source) in app.scan_pending
        assert app.scan_suffix[str(source)] == ["tag"]
        assert source.read_text(encoding="utf-8") == "keep"


async def test_compact_batch_bar_stays_visible_with_expanded_controls(tmp_path: Path) -> None:
    (tmp_path / "photo.txt").write_text("keep", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        for group in app.query("#options Collapsible"):
            group.collapsed = False
        await pilot.pause()
        bar = app.query_one("#batch-bar")
        assert app.query_one("#files").size.height >= 2
        assert bar.region.bottom <= app.size.height
        for ident in ("act-apply", "act-undo", "act-result"):
            assert bar.content_region.contains_region(app.query_one(f"#{ident}").region)
        assert app.query_one("#pending-summary").size.height == 3
        table = app.query_one("#files")
        used = sum(column.width + 2 * table.cell_padding for column in table.columns.values())
        assert used <= table.scrollable_content_region.width


async def test_result_lists_collisions_failures_and_undo_then_retry(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    existing = tmp_path / "target.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    existing.write_text("existing", encoding="utf-8")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._do_apply([(first, "target.txt"), (second, "absent/target.txt")], [])
        assert "target-01.txt" in app._last_result
        assert "destination collisions" in app._last_result
        assert "Failed: second.txt" in app._last_result
        assert "Session undo: rename" in app._last_result
        assert "Undo rename" in str(app.query_one("#act-undo", Button).label)
        assert existing.read_text(encoding="utf-8") == "existing"
        app.action_last_result()
        await pilot.pause()
        assert isinstance(app.screen, ResultScreen)
        assert "second.txt" in str(app.screen.query_one("#result-details").render())
        await pilot.press("escape")
        await pilot.pause()
        app.action_undo()
        await pilot.pause()
        assert first.read_text(encoding="utf-8") == "first"
        assert second.read_text(encoding="utf-8") == "second"
        assert existing.read_text(encoding="utf-8") == "existing"
        app._do_apply([(second, "retry.txt")], [])
        assert (tmp_path / "retry.txt").read_text(encoding="utf-8") == "second"
        assert "Renamed: second.txt → retry.txt" in app._last_result
        app.action_undo()
        await pilot.pause()
        assert second.exists()


async def test_apply_keeps_notes_errors_and_reports_notes_undo_limit(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "photo.jpg"
    photo(source)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.scan_sessions[str(source)] = ScanSession()
        monkeypatch.setattr(app, "_write_scan_notes", lambda _paths: ["photo.jpg: notes disk full"])
        app._do_apply([(source, "renamed.jpg")], [])
        assert "Failed: photo.jpg: notes disk full" in app._last_result
        assert "notes contents are not restored" in app._last_result
        app.action_undo()
        await pilot.pause()
        assert source.exists()
        assert "notes contents are not restored" in app._last_result


async def test_notes_only_result_has_no_undo_claim(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    photo(source)
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.scan_sessions[str(source)] = ScanSession()
        app._do_apply([], [PendingScan(source)])
        assert "No undo entry was created" in app._last_result
        assert "newly created notes may remain" in app._last_result
        assert not app.journal.batches
        assert app.query_one("#act-undo", Button).disabled


async def test_copy_failed_coverage_names_the_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "clip.mov"
    source.write_bytes(b"fixture")
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        stat = source.stat()
        snapshot = MetaOp(source, stat.st_atime, stat.st_mtime, None, "copy", copy_failed=True)
        monkeypatch.setattr(app.journal, "snapshot", lambda _path: snapshot)
        monkeypatch.setattr("tui_renamer.app.set_filesystem_times", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(
            "tui_renamer.app.write_file_metadata_and_offset", lambda *_args, **_kwargs: True
        )
        moment = datetime(2020, 1, 1, tzinfo=timezone.utc)
        app._do_apply([], [PendingScan(source, moment=moment)])
        assert "clip.mov: metadata backup failed" in app._last_result
        assert "only file times can be restored" in app._last_result
        assert "metadata backup failed" in app.query_one("#act-undo", Button).tooltip


async def test_partial_date_write_keeps_snapshot_for_undo(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "2020-01-01-photo.jpg"
    photo(source)
    before = source.stat().st_mtime
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        def partial(path, _moment, **_kwargs):
            import os

            os.utime(path, (1000, 1000))
            return False

        monkeypatch.setattr("tui_renamer.app.set_filesystem_times", partial)
        item = plan_filename_dates([source], None).todo[0]
        app._do_dates([item])
        assert "Partial write" in app._last_result
        assert app.journal.batches[-1].ops
        app.action_undo()
        await pilot.pause()
        assert abs(source.stat().st_mtime - before) < 1


async def test_saved_reading_setup_can_expand_without_reading_again(tmp_path: Path) -> None:
    from tui_renamer.scan_backend import MockBackend
    from tui_renamer.scan_config import ScanSettings, save_scan_settings

    source = tmp_path / "photo.jpg"
    photo(source)
    save_scan_settings(ScanSettings(model="mock"))
    backend = MockBackend(vision_models=["mock"])
    app = RenamerApp(tmp_path)
    app.scan_backend = backend
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        review = app.screen
        await review.workers.wait_for_complete()
        await pilot.pause()
        setup = review.query_one("#scan-reading-setup", Collapsible)
        assert setup.collapsed
        assert "mock" in setup.title
        assert len(backend.recognize_calls) == 1
        review.action_installed()
        await pilot.pause()
        assert not setup.collapsed
        assert len(backend.recognize_calls) == 1
        assert not source.with_name("photo.jpg.rn-notes.json").exists()


async def test_existing_notes_follow_rename_with_explicit_undo_limit(tmp_path: Path) -> None:
    import json

    from tui_renamer.scan_notes import notes_path_for

    source = tmp_path / "photo.jpg"
    photo(source)
    notes = notes_path_for(source)
    notes.write_text(json.dumps({"format": "rn-notes", "version": 2, "front": source.name}))
    app = RenamerApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._do_apply([(source, "renamed.jpg")], [])
        assert "notes contents are not restored" in app._last_result
        moved = notes_path_for(tmp_path / "renamed.jpg")
        assert moved.exists()
        assert json.loads(moved.read_text())["front"] == "renamed.jpg"
        app.action_undo()
        await pilot.pause()
        assert source.exists()
        assert notes.exists()
        assert "notes contents are not restored" in app._last_result


async def test_resize_during_table_rebuild_updates_compact_layout(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    photo(source)
    app = RenamerApp(tmp_path)
    app.exiftool = None
    app.setfile = None
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        app._begin_rebuild()
        try:
            await pilot.resize_terminal(60, 24)
            assert app.screen.has_class("-compact")
        finally:
            app._end_rebuild()
        await pilot.pause()
        assert app.query_one("#files").size.height >= 2
        assert not app.query_one("#detail").display
        await pilot.resize_terminal(140, 42)
        await pilot.pause()
        assert not app.screen.has_class("-compact")
        assert app.query_one("#detail").display
