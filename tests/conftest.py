"""Keep tests off the real terminal graphics probe."""

from __future__ import annotations

import pytest
from textual.widgets import Static


class FakeImage(Static):
    """Stand-in for textual-image so pilots do not query the terminal."""

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
