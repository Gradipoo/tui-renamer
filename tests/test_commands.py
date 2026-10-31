"""Fuzzy matching and dim reasons for the command overlay."""

from tui_renamer.commands import (
    COMMAND_SPECS,
    CommandContext,
    dim_reason,
    fuzzy_score,
    matching_commands,
)


def _spec(run: str):
    return next(spec for spec in COMMAND_SPECS if spec.run == run)


def _ctx(**kwargs: object) -> CommandContext:
    base = {
        "file_count": 2,
        "selected": 0,
        "locked": 0,
        "undo_count": 0,
        "date_mode": False,
        "mtime_mode": False,
    }
    base.update(kwargs)
    return CommandContext(**base)  # type: ignore[arg-type]


def test_fuzzy_smart_case_prefers_the_label() -> None:
    assert fuzzy_score("", "anything") == 1
    assert fuzzy_score("undo", "u Undo Undo the last batch") > 0
    assert fuzzy_score("UNDO", "u Undo Undo the last batch") == 0
    matches = matching_commands("timezone", _ctx())
    assert matches[0][0].run == "timezone"
    assert matches[0][1] == ""


def test_dim_reasons() -> None:
    empty = _ctx(file_count=0)
    assert dim_reason(_spec("undo"), empty) == "nothing to undo"
    assert dim_reason(_spec("unlock_all"), empty) == "nothing locked"
    assert dim_reason(_spec("apply"), empty) == "no files"
    assert dim_reason(_spec("lock_selected"), _ctx(file_count=3, selected=0)) == "no selection"
    assert dim_reason(_spec("lock"), _ctx(date_mode=True)) == "mode clash"
    assert dim_reason(_spec("date_mode"), _ctx(mtime_mode=True)) == "mode clash"
    assert dim_reason(_spec("undo"), _ctx(undo_count=1)) == ""
    assert dim_reason(_spec("preview"), empty) == ""
