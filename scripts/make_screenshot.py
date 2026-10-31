"""Build a demo folder and save a live rn screenshot.

Sample pictures are drawn with Pillow only: gradients, ellipses, and polygons.
No downloaded photos, stock, or third-party art.

The SVG keeps the Fira Code face Textual's export writes. Fira Code is SIL OFL
1.1. The PNG is the same SVG rasterized with rsvg-convert. If Fira Code is not
installed, rsvg falls back to Hack Nerd Font Mono (MIT) when that face is
present, otherwise the converter's default mono.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from tui_renamer import preview as file_preview
from tui_renamer.app import RenamerApp
from tui_renamer.metadata import write_geodata, write_image_exif_datetime

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SVG = REPO / "docs" / "screenshot.svg"
DEFAULT_PNG = REPO / "docs" / "screenshot.png"
# Half-cell preview, not the grayscale unicode fallback used when stdin is not a tty.
_PNG_FONT = "Hack Nerd Font Mono, Fira Code, DejaVu Sans Mono, monospace"


def _sky(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> None:
    width, height = size
    for y in range(height):
        t = y / max(height - 1, 1)
        color = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        draw.line((0, y, width, y), fill=color)


def _ridge(path: Path) -> None:
    size = (360, 220)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    _sky(draw, size, (40, 90, 210), (170, 210, 255))
    draw.ellipse((230, 18, 300, 88), fill=(255, 230, 90))
    draw.polygon(
        [(0, 220), (40, 140), (110, 175), (170, 90), (250, 160), (320, 70), (360, 220)],
        fill=(35, 70, 55),
    )
    draw.polygon(
        [(20, 220), (90, 150), (150, 190), (210, 120), (280, 185), (340, 130), (360, 220)],
        fill=(20, 45, 40),
    )
    image.save(path, format="JPEG", quality=92)


def _dusk(path: Path) -> None:
    size = (340, 210)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    _sky(draw, size, (255, 110, 40), (40, 20, 80))
    draw.ellipse((120, 50, 200, 130), fill=(255, 200, 70))
    for y in range(140, 210):
        t = (y - 140) / 70
        shade = int(20 + 50 * t)
        draw.line((0, y, 340, y), fill=(shade, 20 + int(10 * t), 70 + int(20 * t)))
    for x in range(0, 340, 18):
        draw.arc((x - 20, 150, x + 40, 200), 0, 180, fill=(80, 140, 200), width=2)
    image.save(path, format="JPEG", quality=92)


def _pines(path: Path) -> None:
    size = (300, 200)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    _sky(draw, size, (120, 190, 255), (220, 240, 255))
    draw.rectangle((0, 160, 300, 200), fill=(60, 90, 40))
    for x, h in ((30, 90), (80, 130), (140, 110), (200, 150), (250, 100)):
        top = 160 - h
        draw.polygon([(x, 165), (x + 28, top), (x + 56, 165)], fill=(10, 90 + x // 8, 30))
        draw.rectangle((x + 24, 165, x + 32, 180), fill=(70, 40, 20))
    image.save(path, format="JPEG", quality=92)


def _shards(path: Path) -> None:
    size = (240, 240)
    image = Image.new("RGB", size, (12, 12, 28))
    draw = ImageDraw.Draw(image)
    colors = ((255, 70, 90), (50, 210, 180), (255, 200, 40), (90, 120, 255), (240, 80, 200))
    boxes = (
        (20, 20, 120, 130),
        (90, 50, 210, 160),
        (30, 120, 150, 220),
        (140, 130, 230, 230),
        (70, 80, 170, 190),
    )
    for box, color in zip(boxes, colors, strict=True):
        draw.ellipse(box, fill=color)
    image.save(path, format="PNG")


def _box(path: Path) -> None:
    size = (220, 160)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    _sky(draw, size, (90, 40, 20), (40, 20, 10))
    draw.rectangle((40, 50, 180, 140), fill=(180, 90, 30), outline=(255, 210, 120), width=4)
    draw.polygon([(40, 50), (110, 15), (180, 50)], fill=(220, 130, 50))
    image.save(path, format="JPEG", quality=90)


def _camera(path: Path) -> None:
    size = (200, 150)
    image = Image.new("RGB", size, (15, 15, 20))
    draw = ImageDraw.Draw(image)
    for i in range(8):
        draw.rectangle((10 + i * 22, 10, 28 + i * 22, 140), fill=(30 + i * 25, 40, 200 - i * 20))
    image.save(path, format="JPEG", quality=90)


def build_demo(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    ridge = folder / "2024-06-01-120000-ridge.jpg"
    _ridge(ridge)
    write_image_exif_datetime(ridge, datetime(2024, 6, 1, 17, 0, tzinfo=timezone.utc))
    write_geodata(ridge, 11.25, 22.5)

    box = folder / "1985-03-12-090000-box.jpg"
    _box(box)
    write_image_exif_datetime(box, datetime(1985, 3, 12, 9, 0, tzinfo=timezone.utc))

    pines = folder / "pines.jpg"
    _pines(pines)
    write_image_exif_datetime(pines, datetime(2023, 8, 14, 18, 45, tzinfo=timezone.utc))
    write_geodata(pines, 8.0, 4.5)

    lake = folder / "lake.jpg"
    _dusk(lake)
    (folder / "lake.jpg.json").write_text(
        json.dumps(
            {
                "photoTakenTime": {
                    "timestamp": "1700000000",
                    "formatted": "Nov 14, 2023, 10:13:20 PM UTC",
                },
                "geoData": {"latitude": 9.5, "longitude": 3.25},
            }
        ),
        encoding="utf-8",
    )

    _camera(folder / "IMG_20240131_142500.jpg")
    _shards(folder / "shards.png")
    (folder / "notes.txt").write_text("A plain sidecar-free file.\n", encoding="utf-8")


def _halfcell_widget() -> type:
    from textual_image.widget import HalfcellImage

    return HalfcellImage


def _write_png(svg_path: Path, png_path: Path) -> None:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise SystemExit("rsvg-convert is required to write a color PNG from the SVG.")
    styled = svg_path.with_name(svg_path.stem + ".raster.svg")
    text = svg_path.read_text(encoding="utf-8")
    text = text.replace("font-family: Fira Code, monospace", f"font-family: {_PNG_FONT}")
    text = text.replace("font-family: 'Fira Code'", f"font-family: {_PNG_FONT}")
    styled.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [converter, "-z", "2", "-o", str(png_path), str(styled)],
        check=False,
        capture_output=True,
        text=True,
    )
    styled.unlink(missing_ok=True)
    if result.returncode != 0 or not png_path.exists():
        raise SystemExit(result.stderr or "rsvg-convert failed")


def _enable_truecolor() -> None:
    """Headless CI/agent shells often set NO_COLOR, which Textual turns into gray."""
    os.environ.pop("NO_COLOR", None)
    if os.environ.get("FORCE_COLOR") in {"0", "false", "False"}:
        os.environ.pop("FORCE_COLOR", None)
    os.environ.setdefault("COLORTERM", "truecolor")
    os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
    if os.environ.get("TERM") in {None, "", "dumb"}:
        os.environ["TERM"] = "xterm-256color"


async def _capture(folder: Path, svg_path: Path, png_path: Path) -> None:
    _enable_truecolor()
    file_preview.load_image_widget = _halfcell_widget
    app = RenamerApp(folder)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.rules.source_tz = "UTC"
        app.rules.use_json_data = True
        app.rules.add_geodata = True
        app._facts.clear()
        app.refresh_view()
        await pilot.pause()
        table = app.query_one("#files")
        names = [path.name for path in app.files]
        target = next((index for index, name in enumerate(names) if "ridge" in name), 0)
        table.move_cursor(row=target)
        app._cursor = target
        app.selected.add(target)
        app.refresh_view()
        await pilot.pause(0.4)
        for _ in range(40):
            if app.query("#preview-image"):
                break
            await pilot.pause(0.05)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(app.export_screenshot(title="rn"), encoding="utf-8")
        _write_png(svg_path, png_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a live rn screenshot.")
    parser.add_argument("--svg", type=Path, default=DEFAULT_SVG)
    parser.add_argument("--png", type=Path, default=DEFAULT_PNG)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rn-shot-") as raw:
        folder = Path(raw)
        build_demo(folder)
        asyncio.run(_capture(folder, args.svg.resolve(), args.png.resolve()))
    size = args.svg.stat().st_size
    if size > 2_000_000:
        print(f"Error: {args.svg} is {size} bytes.", file=sys.stderr)
        return 1
    print(args.svg)
    print(args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
