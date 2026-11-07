"""Related-file candidates for the current photo.

Tiers are deterministic. Size and time closeness never make a Likely match.
Unknown is not the same as disproven. Within a tier, stronger combined
evidence and closer comparable times come first, then a stable name tie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.style import Style
from rich.text import Text

from tui_renamer.core import utc_from_timestamp
from tui_renamer.scan_pairing import (
    BACK_TAGS,
    FRONT_TAGS,
    SIZE_RATIO,
    filename_tag,
    scan_images,
)

TIER_ORDER = {"likely": 0, "possible": 1, "unknown": 2, "unlikely": 3}
TIER_STYLE = {
    "likely": Style(color="green"),
    "possible": Style(color="yellow"),
    "unknown": Style(color="yellow"),
    "unlikely": Style(color="red"),
}
TIME_CLOSE = 120
_TRAILING_NUM = re.compile(r"^(.*?)(\d+)$")


@dataclass(frozen=True)
class FileClocks:
    """Comparable clocks. Capture is omitted unless a real capture fact exists."""

    capture: datetime | None = None
    mtime: datetime | None = None
    birth: datetime | None = None


@dataclass(frozen=True)
class Candidate:
    path: Path
    tier: str
    label: str
    reason: str
    size_similar: bool = False
    rank: int = 0

    def line(self) -> Text:
        style = TIER_STYLE.get(self.tier, Style())
        text = Text()
        text.append(f"[{self.label}] ", style)
        text.append(self.path.name)
        text.append(f"  {self.reason}", Style(dim=True))
        return text


def numeric_stem(stem: str) -> tuple[str, int] | None:
    match = _TRAILING_NUM.match(stem)
    if match is None:
        return None
    prefix, digits = match.group(1), match.group(2)
    if not prefix:
        return None
    return prefix.casefold(), int(digits)


def stems_close(left: str, right: str) -> bool:
    a, b = left.casefold(), right.casefold()
    if a == b:
        return True
    numbered = numeric_stem(left)
    other = numeric_stem(right)
    if numbered and other and numbered[0] == other[0] and abs(numbered[1] - other[1]) == 1:
        return True
    n = 0
    for one, two in zip(a, b, strict=False):
        if one != two:
            break
        n += 1
    shortest = min(len(a), len(b))
    return shortest >= 6 and n >= 6 and n * 10 >= shortest * 6


def aware_delta(left: datetime | None, right: datetime | None) -> int | None:
    if left is None or right is None:
        return None
    if left.tzinfo is None or right.tzinfo is None:
        return None
    try:
        delta = left.astimezone(timezone.utc) - right.astimezone(timezone.utc)
        seconds = abs(delta.total_seconds())
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return int(seconds)


def time_clue(subject: FileClocks, other: FileClocks) -> tuple[int, str] | None:
    """Return (delta_seconds, source label) for one comparable clock pair."""
    capture = aware_delta(subject.capture, other.capture)
    if capture is not None:
        return capture, "capture date"
    mtime = aware_delta(subject.mtime, other.mtime)
    if mtime is not None:
        return mtime, "mtime"
    birth = aware_delta(subject.birth, other.birth)
    if birth is not None:
        return birth, "birth"
    return None


def time_phrase(delta: int, source: str) -> str:
    if delta == 0:
        return f"same {source}"
    unit = "second" if delta == 1 else "seconds"
    return f"{delta} {unit} apart ({source})"


def stat_clocks(path: Path) -> FileClocks:
    try:
        stat = path.stat()
    except OSError:
        return FileClocks()
    mtime = utc_from_timestamp(stat.st_mtime)
    birth_ts = getattr(stat, "st_birthtime", None)
    birth = None if birth_ts is None else utc_from_timestamp(birth_ts)
    return FileClocks(mtime=mtime, birth=birth)


def score_candidates(
    subject: Path,
    files: list[Path],
    *,
    clocks: dict[Path, FileClocks] | None = None,
    size_cache: dict[Path, tuple[int, int] | None] | None = None,
) -> list[Candidate]:
    images = scan_images(files)
    others = [path for path in images if path != subject]
    unique: Path | None = None
    ambiguous: set[Path] = set()
    tagged = filename_tag(subject)
    if tagged is not None:
        base, tag = tagged
        partners: list[Path] = []
        for path in others:
            other = filename_tag(path)
            if other is None or other[0] != base:
                continue
            if tag in FRONT_TAGS and other[1] in BACK_TAGS:
                partners.append(path)
            elif tag in BACK_TAGS and other[1] in FRONT_TAGS:
                partners.append(path)
        if len(partners) == 1:
            unique = partners[0]
        elif len(partners) > 1:
            ambiguous = set(partners)
    neighbors: set[Path] = set()
    if subject in images:
        index = images.index(subject)
        if index > 0:
            neighbors.add(images[index - 1])
        if index + 1 < len(images):
            neighbors.add(images[index + 1])
    sub_clocks = (clocks or {}).get(subject) or FileClocks()
    rows: list[Candidate] = []
    for path in others:
        similar = _similar_size(subject, path, size_cache)
        other_clocks = (clocks or {}).get(path) or FileClocks()
        timed = time_clue(sub_clocks, other_clocks)
        close_time = timed is not None and timed[0] <= TIME_CLOSE
        named = stems_close(subject.stem, path.stem)
        sequenced = _sequence_neighbor(subject.stem, path.stem)
        time_text = time_phrase(timed[0], timed[1]) if timed is not None else ""
        if path == unique:
            reason = "filename partner"
            rank = 1000
            if close_time:
                reason += f", {time_text}"
                rank += _time_boost(timed[0])
            rows.append(Candidate(path, "likely", "Likely", reason, similar, rank))
            continue
        if path in ambiguous:
            rows.append(
                Candidate(
                    path,
                    "possible",
                    "Possible",
                    "filename tag is ambiguous",
                    similar,
                    400,
                )
            )
            continue
        if sequenced or named or path in neighbors:
            bits = []
            rank = 200
            if sequenced:
                bits.append("adjacent filename")
                rank = 320
            elif named:
                bits.append("similar filename")
                rank = 280
            elif path in neighbors:
                bits.append("next file in folder")
                rank = 220
            if close_time and timed is not None:
                bits.append(time_text)
                rank += _time_boost(timed[0])
            elif similar:
                bits.append("similar size (not enough for Likely)")
            rows.append(Candidate(path, "possible", "Possible", ", ".join(bits), similar, rank))
            continue
        if close_time and timed is not None:
            rows.append(
                Candidate(
                    path,
                    "unknown",
                    "Unknown",
                    f"{time_text}, not evidence enough",
                    similar,
                    80 + _time_boost(timed[0]),
                )
            )
            continue
        if similar:
            rows.append(
                Candidate(
                    path,
                    "unknown",
                    "Unknown",
                    "similar size only, not evidence enough",
                    True,
                    10,
                )
            )
            continue
        rows.append(
            Candidate(
                path,
                "unlikely",
                "Unlikely",
                "no name or order link; lack of evidence is not disproven",
                False,
                0,
            )
        )
    rows.sort(
        key=lambda item: (
            TIER_ORDER.get(item.tier, 9),
            -item.rank,
            item.path.name.casefold(),
            str(item.path),
        )
    )
    return rows


def _sequence_neighbor(left: str, right: str) -> bool:
    one = numeric_stem(left)
    two = numeric_stem(right)
    return bool(one and two and one[0] == two[0] and abs(one[1] - two[1]) == 1)


def _time_boost(delta: int) -> int:
    return max(0, TIME_CLOSE - delta)


def _similar_size(
    left: Path,
    right: Path,
    cache: dict[Path, tuple[int, int] | None] | None,
) -> bool:
    box_left = _box(left, cache)
    box_right = _box(right, cache)
    if box_left is None or box_right is None:
        return False
    area_left = box_left[0] * box_left[1]
    area_right = box_right[0] * box_right[1]
    if area_left <= 0 or area_right <= 0:
        return False
    ratio = max(area_left, area_right) / min(area_left, area_right)
    return ratio <= SIZE_RATIO * SIZE_RATIO


def _box(
    path: Path,
    cache: dict[Path, tuple[int, int] | None] | None,
) -> tuple[int, int] | None:
    if cache is not None and path in cache:
        return cache[path]
    from PIL import Image

    try:
        with Image.open(path) as image:
            box = image.size
    except Exception:
        box = None
    if cache is not None:
        cache[path] = box
    return box
