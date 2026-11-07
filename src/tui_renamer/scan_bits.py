"""Editable result chips staged into a name or metadata plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from tui_renamer.scan_dates import ScanDate, can_queue_capture

MAX_SLUG = 40
MAX_CHIPS = 24
MAX_LABEL = 80


@dataclass
class Bit:
    """One extracted fact. Click cycles off, name, then metadata when allowed."""

    kind: str
    label: str
    source: str
    model: str
    origin: str
    use_name: bool = False
    use_meta: bool = False
    date: ScanDate | None = None
    people_count: int | None = None
    people_present: bool | None = None
    manual: bool = False

    def tooltip(self) -> str:
        origin = "written" if self.origin == "written" else "inferred"
        return f"{self.kind}. Source {self.source}. {origin}. Model {self.model or 'manual'}."

    def caption(self) -> str:
        flags = []
        if self.use_name:
            flags.append("name")
        if self.use_meta:
            flags.append("meta")
        prefix = "+".join(flags) + " " if flags else ""
        return f"{prefix}{self.label}"[:MAX_LABEL]

    def can_meta(self) -> bool:
        if self.kind == "date":
            return self.date is not None and can_queue_capture(self.date)
        return self.kind in {"token", "text"}

    def cycle(self) -> None:
        if not self.use_name and not self.use_meta:
            self.use_name = True
            return
        if self.use_name and not self.use_meta and self.can_meta():
            self.use_meta = True
            return
        self.use_name = False
        self.use_meta = False


@dataclass
class PendingScan:
    path: Path
    moment: datetime | None = None
    noon: bool = False
    description: str = ""
    date_text: str = ""
    overwrite: bool = False


@dataclass
class StageResult:
    message: str
    subject: Path
    new_name: str | None = None
    pending: PendingScan | None = None


def slug_piece(text: str) -> str:
    piece = re.sub(r"[^A-Za-z0-9]+", "-", text.strip())
    return piece.strip("-")[:MAX_SLUG]


def staged_filename(original: Path, bits: list[Bit]) -> str | None:
    parts = [slug_piece(item.label) for item in bits if item.use_name]
    parts = [item for item in parts if item]
    if not parts:
        return None
    stem = original.stem
    extra = "-".join(parts)
    if extra.casefold() in stem.casefold():
        return original.name
    return f"{stem}-{extra}{original.suffix}"


def capture_from_bits(bits: list[Bit], zone: tzinfo) -> tuple[datetime | None, str]:
    """One written calendar day, or None. Never fills year or month to a day."""
    days = [
        item.date
        for item in bits
        if item.use_meta
        and item.kind == "date"
        and item.date is not None
        and can_queue_capture(item.date)
    ]
    if len(days) != 1:
        return None, ""
    date = days[0]
    assert date.year is not None and date.month is not None and date.day is not None
    moment = datetime(date.year, date.month, date.day, 12, 0, 0, tzinfo=zone)
    return moment, date.text


def description_from_bits(bits: list[Bit]) -> str:
    parts = [
        item.label.strip() for item in bits if item.use_meta and item.kind in {"token", "text"}
    ]
    return "; ".join(part for part in parts if part)[:200]


def bits_from_recognition(
    *,
    transcription: str,
    dates: list[ScanDate],
    tokens: list[str],
    people_present: bool | None,
    people_count: int | None,
    source: str,
    model: str,
) -> list[Bit]:
    bits: list[Bit] = []
    if transcription.strip():
        bits.append(
            Bit(
                kind="text",
                label=transcription.strip()[:MAX_LABEL],
                source=source,
                model=model,
                origin="written",
            )
        )
    for date in dates:
        bits.append(
            Bit(
                kind="date",
                label=date.text.strip()[:MAX_LABEL],
                source=source,
                model=model,
                origin=date.origin,
                date=date,
            )
        )
    for token in tokens:
        clean = token.strip()
        if not clean:
            continue
        bits.append(
            Bit(
                kind="token",
                label=clean[:MAX_LABEL],
                source=source,
                model=model,
                origin="inferred",
            )
        )
    if people_present is True or people_count is not None:
        if people_count is not None and people_count >= 0:
            label = f"{people_count} people" if people_count != 1 else "1 person"
        else:
            label = "people present"
        bits.append(
            Bit(
                kind="people",
                label=label,
                source=source,
                model=model,
                origin="inferred",
                people_count=people_count,
                people_present=True if people_present is None else people_present,
            )
        )
    return bits[:MAX_CHIPS]


def dump_bits(bits: list[Bit]) -> list[dict]:
    payload = []
    for item in bits:
        payload.append(
            {
                "kind": item.kind,
                "label": item.label[:MAX_LABEL],
                "source": item.source,
                "model": item.model,
                "origin": item.origin,
                "use_name": item.use_name,
                "use_meta": item.use_meta,
                "manual": item.manual,
                "people_count": item.people_count,
                "people_present": item.people_present,
                "date": None
                if item.date is None
                else {
                    "kind": item.date.kind,
                    "text": item.date.text,
                    "year": item.date.year,
                    "month": item.date.month,
                    "day": item.date.day,
                    "season": item.date.season,
                    "uncertain": item.date.uncertain,
                    "origin": item.date.origin,
                },
            }
        )
    return payload


def load_bits(raw: object) -> list[Bit]:
    if not isinstance(raw, list):
        return []
    bits: list[Bit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        kind = item.get("kind")
        if not isinstance(label, str) or not label.strip():
            continue
        if kind not in {"date", "text", "token", "people"}:
            continue
        origin = item.get("origin")
        if origin not in {"written", "inferred"}:
            origin = "written"
        source = item.get("source")
        model = item.get("model")
        date = None
        date_raw = item.get("date")
        if isinstance(date_raw, dict):
            from tui_renamer.scan_dates import parse_model_dates

            parsed = parse_model_dates([date_raw])
            date = parsed[0] if parsed else None
        count = item.get("people_count")
        bits.append(
            Bit(
                kind=kind,
                label=label.strip()[:MAX_LABEL],
                source=source if isinstance(source, str) else "",
                model=model if isinstance(model, str) else "",
                origin=origin,
                use_name=item.get("use_name") is True,
                use_meta=item.get("use_meta") is True,
                date=date,
                people_count=count
                if isinstance(count, int) and not isinstance(count, bool)
                else None,
                people_present=item.get("people_present")
                if isinstance(item.get("people_present"), bool)
                else None,
                manual=item.get("manual") is True,
            )
        )
    return bits[:MAX_CHIPS]
