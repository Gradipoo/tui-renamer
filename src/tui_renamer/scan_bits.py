"""Editable result chips staged into a name or metadata plan."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING

from tui_renamer.scan_dates import ScanDate, can_queue_capture

if TYPE_CHECKING:
    from tui_renamer.core import RenameRules

MAX_SLUG = 40
MAX_CHIPS = 24
MAX_LABEL = 80


@dataclass
class Bit:
    """One extracted fact. Name and metadata are independent on/off flags."""

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
    name_order: int = 0

    def tooltip(self) -> str:
        origin = "written" if self.origin == "written" else "inferred"
        return f"{self.kind}. Source {self.source}. {origin}. Model {self.model or 'manual'}."

    def caption(self) -> str:
        mark = "name " if self.use_name else ""
        return f"{mark}{self.label}"[:MAX_LABEL]

    def can_meta(self) -> bool:
        if self.kind == "date":
            return self.date is not None and can_queue_capture(self.date)
        return self.kind in {"token", "text"}

    def toggle_name(self) -> None:
        self.use_name = not self.use_name
        if not self.use_name:
            self.name_order = 0

    def toggle_meta(self) -> None:
        if not self.can_meta():
            return
        self.use_meta = not self.use_meta


@dataclass
class PendingScan:
    path: Path
    moment: datetime | None = None
    noon: bool = False
    description: str = ""
    date_text: str = ""
    overwrite: bool = False


@dataclass
class ScanSession:
    """In-memory review state. The notes sidecar is written on Apply."""

    bits: list[Bit] = field(default_factory=list)
    suffix_parts: list[str] = field(default_factory=list)
    engine: str = ""
    raw: str = ""
    model: str = ""
    related_name: str | None = None
    related_reason: str = ""
    overwrite: bool = False


@dataclass
class StageResult:
    message: str
    subject: Path
    suffix_parts: list[str] = field(default_factory=list)
    pending: PendingScan | None = None
    session: ScanSession | None = None


def selected_labels(bits: list[Bit]) -> list[str]:
    chosen = [(index, item) for index, item in enumerate(bits) if item.use_name]
    chosen.sort(key=lambda pair: (pair[1].name_order or 0, pair[0]))
    return [item.label.strip() for _index, item in chosen if item.label.strip()]


def format_scan_part(text: str, rules: RenameRules) -> str:
    """Apply ordinary filename format toggles to one S tag. Labels stay raw."""
    from tui_renamer.core import clean_filename

    piece = text.replace("\0", "").replace("/", " ").replace("\\", " ").strip()
    piece = clean_filename(piece, rules)
    return piece[:MAX_SLUG]


def format_scan_parts(labels: list[str], rules: RenameRules) -> list[str]:
    return [part for part in (format_scan_part(item, rules) for item in labels) if part]


def strip_accepted_suffix(stem: str, accepted: list[str]) -> str:
    """Remove a trailing suffix only when it equals the known accepted parts."""
    parts = [item for item in accepted if item]
    if not stem or not parts:
        return stem
    extra = "-".join(parts)
    if stem == extra:
        return ""
    token = "-" + extra
    if stem.endswith(token):
        return stem[: -len(token)]
    return stem


def join_scan_name(stem: str, ext: str, parts: list[str], *, fallback: str) -> str:
    extra = "-".join(part for part in parts if part)
    extra = re.sub(r"--+", "-", extra).strip("-_")
    if stem and extra:
        return f"{stem}-{extra}{ext}"
    if extra:
        return f"{extra}{ext}"
    if stem:
        return f"{stem}{ext}"
    return fallback


def apply_scan_suffix(
    filename: str,
    parts: list[str],
    *,
    accepted: list[str] | None = None,
    stem: str | None = None,
) -> str:
    """Append ordered S tags. Empty stem plus tags is tags plus extension."""
    file_stem, ext = os.path.splitext(filename)
    stem_part = file_stem if stem is None else stem
    stem_part = strip_accepted_suffix(stem_part, accepted or [])
    return join_scan_name(stem_part, ext, parts, fallback=filename)


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
                "name_order": item.name_order,
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
        order = item.get("name_order")
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
                name_order=order
                if isinstance(order, int) and not isinstance(order, bool) and order > 0
                else 0,
            )
        )
    return bits[:MAX_CHIPS]
