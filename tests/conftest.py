"""Keep tests off the real terminal graphics probe and the real view config."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Static


class FakeImage(Static):
    """Stand-in for textual-image so pilots do not query the terminal.

    ``mounted_ok`` stays false when Textual rejects the widget (a reused id
    while the previous picture is still being removed). Pilots assert it.
    """

    def __init__(
        self,
        image: object = None,
        *,
        id: str | None = None,
        on_error: object = None,
    ) -> None:
        super().__init__("image", id=id)
        self.on_error = on_error
        self._picture = image
        self.mounted_ok = False

    def on_mount(self) -> None:
        self.mounted_ok = True

    @property
    def image(self) -> object:
        return self._picture

    @image.setter
    def image(self, value: object) -> None:
        self._picture = value
        self.update("image")


@pytest.fixture(autouse=True)
def _fake_image_widget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tui_renamer.preview.load_image_widget", lambda: FakeImage)


@pytest.fixture(autouse=True)
def _isolated_view_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "rn-config"
    monkeypatch.setattr("tui_renamer.view_config.config_dir", lambda: folder)


async def wait_for_folder_load(app, pilot) -> None:
    """Wait for the folder snapshot, rather than assuming UI idle means loaded."""
    workers = [worker for worker in app.workers if worker.group == "folder-load"]
    if workers:
        await asyncio.wait_for(app.workers.wait_for_complete(workers), timeout=15)
    async with asyncio.timeout(15):
        await pilot.pause()
        # The result message can schedule a rebuild after the worker has finished.
        while app._loading or app._rebuilding:
            await pilot.pause()


async def wait_for_reconcile_load(app, pilot) -> None:
    """Wait for the active earliest-date snapshot and its table refresh."""
    async with asyncio.timeout(15):
        await pilot.pause()
        while app._reconcile_loading or app._rebuilding:
            await asyncio.sleep(0.01)
            await pilot.pause()
