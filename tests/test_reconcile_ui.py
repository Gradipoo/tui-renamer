"""Headless timestamp batch review interactions."""

from pathlib import Path

from textual.app import App
from textual.widgets import Button, OptionList, Select

from tui_renamer.reconcile_ui import ReconcileResult, ReconcileScreen


async def test_reconcile_preserves_each_rows_source_and_refreshes_only_on_changes():
    app = App()
    reviews: list[ReconcileResult] = []
    answers: list[ReconcileResult | None] = []

    def describe(selected, filenames, filesystem, capture):
        reviews.append((selected, filenames, filesystem, capture))
        return (
            "Two rows",
            [str(choice) for choice in selected],
            any(choice is not None for choice in selected),
        )

    screen = ReconcileScreen(
        [Path("a.jpg"), Path("b.jpg")],
        [["Capture date", "Filename date"], ["Sidecar date"]],
        describe,
        initial=[0, None],
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await app.push_screen(screen, answers.append)
        await pilot.pause()
        assert screen.focused is screen.query_one("#reconcile-cancel", Button)
        assert reviews[-1] == ([0, None], True, True, False)

        rows = screen.query_one("#reconcile-rows", OptionList)
        source = screen.query_one("#reconcile-source", Select)
        count = len(reviews)
        rows.highlighted = 1
        await pilot.pause()
        assert len(reviews) == count

        source.value = 0
        await pilot.pause()
        assert reviews[-1] == ([0, 0], True, True, False)
        rows.highlighted = 0
        await pilot.pause()
        source.value = 1
        await pilot.pause()

        await pilot.click("#reconcile-capture")
        await pilot.click("#reconcile-apply")
        await pilot.pause()
        assert answers == [([1, 0], True, True, True)]


async def test_reconcile_disables_apply_and_enter_cancels_without_a_plan():
    app = App()
    answers: list[ReconcileResult | None] = []
    screen = ReconcileScreen(
        [Path("a.jpg")],
        [["Capture date", "Filename date"]],
        lambda *_: ("Choose a timestamp source", ["Conflicting sources"], False),
        initial=[None],
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await app.push_screen(screen, answers.append)
        await pilot.pause()
        assert screen.query_one("#reconcile-apply", Button).disabled
        await pilot.press("enter")
        await pilot.pause()
        assert answers == [None]
