"""Build a demo folder and save docs/screenshot.svg from a live rn session."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from tui_renamer.app import RenamerApp
from tui_renamer.metadata import write_geodata, write_image_exif_datetime

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SVG = REPO / "docs" / "screenshot.svg"
DEFAULT_PNG = REPO / "docs" / "screenshot.png"


def _photo(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (96, 64)) -> None:
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, size[0] - 9, size[1] - 9), outline=(250, 250, 250))
    image.save(path, format="JPEG", quality=90)


def build_demo(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    harbor = folder / "2024-06-01-120000-harbor.jpg"
    _photo(harbor, (30, 90, 160), (120, 80))
    write_image_exif_datetime(harbor, datetime(2024, 6, 1, 17, 0, tzinfo=timezone.utc))
    write_geodata(harbor, 47.61, -122.33)

    archive = folder / "1985-03-12-090000-archive.jpg"
    _photo(archive, (90, 60, 40), (80, 60))
    write_image_exif_datetime(archive, datetime(1985, 3, 12, 9, 0, tzinfo=timezone.utc))

    trail = folder / "trail.jpg"
    _photo(trail, (40, 120, 70), (88, 66))
    write_image_exif_datetime(trail, datetime(2023, 8, 14, 18, 45, tzinfo=timezone.utc))
    write_geodata(trail, 45.52, -122.68)

    plaza = folder / "plaza.jpg"
    _photo(plaza, (180, 90, 40), (100, 70))

    camera = folder / "IMG_20240131_142500.jpg"
    _photo(camera, (20, 20, 20), (72, 54))

    untitled = folder / "untitled.png"
    Image.new("RGB", (48, 48), (200, 80, 90)).save(untitled, format="PNG")

    (folder / "notes.txt").write_text("A plain sidecar-free file.\n", encoding="utf-8")
    (folder / "plaza.jpg.json").write_text(
        json.dumps(
            {
                "photoTakenTime": {
                    "timestamp": "1700000000",
                    "formatted": "Nov 14, 2023, 10:13:20 PM UTC",
                },
                "geoData": {"latitude": 37.77, "longitude": -122.42},
            }
        ),
        encoding="utf-8",
    )


def _sanitize_svg(svg: str) -> str:
    """Drop remote fonts and fixed text widths so GitHub can show the picture."""
    svg = re.sub(r"@font-face\s*\{[^}]*\}", "", svg, flags=re.DOTALL)
    svg = re.sub(r"url\([^)]+\)", "", svg)
    svg = re.sub(r'\s+textLength="[^"]*"', "", svg)
    svg = svg.replace(
        "font-family: Fira Code, monospace",
        "font-family: ui-monospace, Menlo, Consolas, monospace",
    )
    svg = svg.replace(
        "font-family: 'Fira Code'", "font-family: ui-monospace, Menlo, Consolas, monospace"
    )
    svg = svg.replace(
        'font-family="Fira Code"', 'font-family="ui-monospace, Menlo, Consolas, monospace"'
    )
    svg = re.sub(r"<script[\s\S]*?</script>", "", svg, flags=re.IGNORECASE)
    return svg


def _mono_font() -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        r"C:\Windows\Fonts\consola.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, 16)
    return ImageFont.load_default()


def _rgb(color: object, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if color is None or str(color) in {"default", "none"}:
        return fallback
    triplet = getattr(color, "triplet", None)
    if triplet is not None:
        return (triplet.red, triplet.green, triplet.blue)
    return fallback


def _write_png(app: RenamerApp, png_path: Path) -> None:
    """Paint the live screen so the PNG does not depend on a remote font."""
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    screen = app.screen._compositor.render_update(
        full=True, screen_stack=app.app._background_screens
    )
    lines = console.render_lines(screen, console.options)
    font = _mono_font()
    sample = font.getbbox("M")
    cell_w = max(8, sample[2] - sample[0])
    cell_h = max(16, sample[3] - sample[1] + 6)
    image = Image.new("RGB", (cell_w * width, cell_h * height), (16, 16, 20))
    draw = ImageDraw.Draw(image)
    for row, segments in enumerate(lines):
        col = 0
        for segment in segments:
            text, style, control = segment
            if control or not text:
                continue
            fg = _rgb(getattr(style, "color", None), (220, 220, 220))
            bg = _rgb(getattr(style, "bgcolor", None), (16, 16, 20))
            x = col * cell_w
            y = row * cell_h
            draw.rectangle((x, y, x + cell_w * len(text), y + cell_h), fill=bg)
            draw.text((x, y + 1), text, font=font, fill=fg)
            col += len(text)
    image.save(png_path, format="PNG")


async def _capture(folder: Path, svg_path: Path, png_path: Path) -> None:
    app = RenamerApp(folder)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.rules.source_tz = "UTC"
        app.rules.use_json_data = True
        app._facts.clear()
        app.refresh_view()
        await pilot.pause()
        table = app.query_one("#files")
        names = [path.name for path in app.files]
        target = next(
            (index for index, name in enumerate(names) if "harbor" in name),
            0,
        )
        table.move_cursor(row=target)
        app._cursor = target
        app.selected.add(target)
        app.refresh_view()
        await pilot.pause(0.4)
        for _ in range(40):
            caption = str(app.query_one("#preview-caption").render())
            if "harbor" in caption or "jpg" in caption.lower():
                break
            await pilot.pause(0.05)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        raw = app.export_screenshot(title="rn")
        svg_path.write_text(_sanitize_svg(raw), encoding="utf-8")
        _write_png(app, png_path)


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
    if args.png.exists():
        print(args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
