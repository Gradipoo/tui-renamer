"""Date precision for handwritten scan backs.

Partial dates stay partial. A year is never turned into a day. Scene-based
era guesses are out of scope: only text the user or a backend supplied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEASONS = {
    "spring": "spring",
    "summer": "summer",
    "autumn": "autumn",
    "fall": "autumn",
    "winter": "winter",
}
KINDS = frozenset({"year", "month", "day", "season", "range"})
_YEAR = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
_ISO_DAY = re.compile(r"\b(1[6-9]\d{2}|20\d{2})-(\d{1,2})-(\d{1,2})\b")
_ISO_MONTH = re.compile(r"\b(1[6-9]\d{2}|20\d{2})-(\d{1,2})\b")
_OR = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\s+or\s+(1[6-9]\d{2}|20\d{2})\b", re.I)


@dataclass(frozen=True)
class ScanDate:
    """One date reading. Alternatives are separate ScanDate values."""

    kind: str
    text: str
    year: int | None = None
    month: int | None = None
    day: int | None = None
    season: str | None = None
    uncertain: bool = False

    def as_line(self) -> str:
        return self.text.strip()


def parse_date_text(blob: str) -> list[ScanDate]:
    """Conservative readings from user or model text. Empty when nothing is dated."""
    text = blob.strip()
    if not text:
        return []
    found: list[ScanDate] = []
    for match in _OR.finditer(text):
        left, right = int(match.group(1)), int(match.group(2))
        found.append(ScanDate("year", match.group(0), year=left, uncertain=True))
        found.append(ScanDate("year", match.group(0), year=right, uncertain=True))
    if found:
        return _dedupe(found)
    for match in _ISO_DAY.finditer(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _valid_day(year, month, day):
            found.append(ScanDate("day", match.group(0), year=year, month=month, day=day))
    if found:
        return _dedupe(found)
    for match in _ISO_MONTH.finditer(text):
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            found.append(ScanDate("month", match.group(0), year=year, month=month))
    if found:
        return _dedupe(found)
    season = _season(text)
    years = [int(match.group(1)) for match in _YEAR.finditer(text)]
    uncertain = "or" in text.casefold() or "?" in text
    if season and len(years) == 1:
        found.append(
            ScanDate(
                "season",
                text,
                year=years[0],
                season=season,
                uncertain=uncertain,
            )
        )
        return found
    if season and not years:
        found.append(ScanDate("season", text, season=season, uncertain=uncertain))
        return found
    for year in years:
        found.append(ScanDate("year", str(year), year=year, uncertain=uncertain))
    return _dedupe(found)


def parse_model_dates(raw: object) -> list[ScanDate]:
    """Keep only well-formed date objects. Drop invented finer precision."""
    if not isinstance(raw, list):
        return []
    found: list[ScanDate] = []
    for item in raw:
        parsed = _one_model_date(item)
        if parsed is not None:
            found.append(parsed)
    return _dedupe(found)


def _one_model_date(item: object) -> ScanDate | None:
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 80:
        return None
    kind = item.get("kind")
    if kind not in KINDS:
        return None
    year = _opt_int(item.get("year"), 1600, 2100)
    month = _opt_int(item.get("month"), 1, 12)
    day = _opt_int(item.get("day"), 1, 31)
    season_raw = item.get("season")
    season = None
    if isinstance(season_raw, str):
        season = SEASONS.get(season_raw.strip().casefold())
    uncertain = item.get("uncertain") is True
    if kind == "day":
        if year is None or month is None or day is None or not _valid_day(year, month, day):
            return None
        return ScanDate("day", text.strip(), year, month, day, None, uncertain)
    if kind == "month":
        if year is None or month is None:
            return None
        return ScanDate("month", text.strip(), year, month, None, None, uncertain)
    if kind == "season":
        if season is None:
            return None
        return ScanDate("season", text.strip(), year, None, None, season, uncertain)
    if kind == "year":
        if year is None:
            return None
        return ScanDate("year", text.strip(), year, None, None, None, uncertain)
    if kind == "range":
        if year is None:
            return None
        return ScanDate("range", text.strip(), year, None, None, None, True)
    return None


def _valid_day(year: int, month: int, day: int) -> bool:
    if month < 1 or month > 12 or day < 1 or day > 31:
        return False
    try:
        from datetime import date

        date(year, month, day)
    except ValueError:
        return False
    return True


def _opt_int(value: object, lo: int, hi: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < lo or value > hi:
        return None
    return value


def _season(text: str) -> str | None:
    folded = text.casefold()
    for name, canon in SEASONS.items():
        if re.search(rf"\b{name}\b", folded):
            return canon
    return None


def _dedupe(items: list[ScanDate]) -> list[ScanDate]:
    seen: set[tuple[object, ...]] = set()
    unique: list[ScanDate] = []
    for item in items:
        key = (item.kind, item.year, item.month, item.day, item.season, item.uncertain)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
