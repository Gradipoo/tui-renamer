"""Suggest front/back pairs from names and scan order.

Filename tags win. Neighboring files are a suggestion only. Similar pixel
size is a weak tie-breaker. It never confirms a pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp", ".bmp"}
)
_TAG = re.compile(
    r"^(?P<base>.+?)(?P<sep>[_-])(?P<tag>a|b|front|back|recto|verso)$",
    re.IGNORECASE,
)
FRONT_TAGS = frozenset({"a", "front", "recto"})
BACK_TAGS = frozenset({"b", "back", "verso"})
SIZE_RATIO = 1.15


@dataclass
class PairRecord:
    front: Path
    back: Path | None
    reason: str
    confirmed: bool = False
    size_similar: bool = False
    ambiguous: bool = False
    others: list[Path] = field(default_factory=list)


def scan_images(files: list[Path]) -> list[Path]:
    return [path for path in files if path.suffix.lower() in IMAGE_SUFFIXES]


def filename_tag(path: Path) -> tuple[str, str] | None:
    match = _TAG.match(path.stem)
    if match is None:
        return None
    return match.group("base").casefold(), match.group("tag").casefold()


def suggest_pairs(files: list[Path]) -> list[PairRecord]:
    """Suggested pairs. Nothing is confirmed. Ambiguous filename matches stay open."""
    images = scan_images(files)
    used: set[Path] = set()
    records: list[PairRecord] = []
    by_base: dict[str, list[tuple[Path, str]]] = {}
    for path in images:
        tagged = filename_tag(path)
        if tagged is None:
            continue
        base, tag = tagged
        by_base.setdefault(base, []).append((path, tag))
    for _base, items in by_base.items():
        fronts = [path for path, tag in items if tag in FRONT_TAGS]
        backs = [path for path, tag in items if tag in BACK_TAGS]
        if len(fronts) == 1 and len(backs) == 1:
            front, back = fronts[0], backs[0]
            records.append(
                PairRecord(
                    front=front,
                    back=back,
                    reason="filename",
                    size_similar=sizes_similar(front, back),
                )
            )
            used.add(front)
            used.add(back)
        elif fronts and backs:
            for front in fronts:
                records.append(
                    PairRecord(
                        front=front,
                        back=None,
                        reason="filename",
                        ambiguous=True,
                        others=list(backs),
                    )
                )
                used.add(front)
            used.update(backs)

    leftover = [path for path in images if path not in used]
    index = 0
    while index < len(leftover):
        left = leftover[index]
        if index + 1 >= len(leftover):
            records.append(PairRecord(front=left, back=None, reason="none"))
            break
        right = leftover[index + 1]
        records.append(
            PairRecord(
                front=left,
                back=right,
                reason="neighbor",
                size_similar=sizes_similar(left, right),
            )
        )
        index += 2
    return records


def sizes_similar(left: Path, right: Path) -> bool:
    """True when both images open and their pixel boxes are close.

    This is only a hint. Callers must not treat it as confirmation.
    """
    box_left = _size(left)
    box_right = _size(right)
    if box_left is None or box_right is None:
        return False
    area_left = box_left[0] * box_left[1]
    area_right = box_right[0] * box_right[1]
    if area_left <= 0 or area_right <= 0:
        return False
    ratio = max(area_left, area_right) / min(area_left, area_right)
    return ratio <= SIZE_RATIO * SIZE_RATIO


def _size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None
