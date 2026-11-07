"""Versioned notes sidecars for accepted photo analysis.

The file name is ``<image.name>.rn-notes.json``. That spelling is not in the
Google Takeout candidate list. The payload also carries ``format: rn-notes`` so
a misnamed file cannot be ingested as a rename date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tui_renamer.scan_bits import Bit, dump_bits, load_bits
from tui_renamer.scan_dates import ScanDate, parse_model_dates

FORMAT = "rn-notes"
VERSION = 2
SUFFIX = ".rn-notes.json"
MAX_TEXT = 8000


@dataclass
class ScanNotes:
    front: str
    back: str | None
    pairing_reason: str
    pairing_confirmed: bool
    engine: str
    model: str
    raw_transcription: str
    notes: str
    dates: list[ScanDate] = field(default_factory=list)
    accepted_at: str = ""
    size_similar: bool = False
    bits: list[Bit] = field(default_factory=list)
    related_reason: str = ""
    name_suffix: list[str] = field(default_factory=list)


def notes_path_for(path: Path) -> Path:
    return path.with_name(path.name + SUFFIX)


def is_notes_filename(path: Path) -> bool:
    return path.name.endswith(SUFFIX)


def is_notes_document(data: object) -> bool:
    return isinstance(data, dict) and data.get("format") == FORMAT


def is_notes_sidecar(path: Path) -> bool:
    """True when this file is a notes sidecar by name or payload."""
    if is_notes_filename(path):
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    return is_notes_document(data)


def takeout_sidecar(path: Path, candidates: list[Path]) -> Path | None:
    """First existing Takeout-style sidecar that is not a notes document."""
    for item in candidates:
        if not item.exists():
            continue
        if is_notes_sidecar(item):
            continue
        return item
    return None


def dump_notes(notes: ScanNotes) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "front": notes.front,
        "back": notes.back,
        "pairing": {
            "reason": notes.pairing_reason,
            "confirmed": notes.pairing_confirmed,
            "size_similar": notes.size_similar,
            "related_reason": notes.related_reason,
        },
        "recognition": {
            "engine": notes.engine,
            "model": notes.model,
            "raw_transcription": notes.raw_transcription[:MAX_TEXT],
        },
        "notes": notes.notes[:MAX_TEXT],
        "dates": [
            {
                "kind": item.kind,
                "text": item.text,
                "year": item.year,
                "month": item.month,
                "day": item.day,
                "season": item.season,
                "uncertain": item.uncertain,
                "origin": item.origin,
            }
            for item in notes.dates
        ],
        "bits": dump_bits(notes.bits),
        "name_suffix": [item for item in notes.name_suffix if isinstance(item, str)][:24],
        "accepted_at": notes.accepted_at,
    }


def load_notes(path: Path) -> ScanNotes | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not is_notes_document(data):
        return None
    version = data.get("version")
    if version not in {1, 2}:
        return None
    pairing = data.get("pairing") if isinstance(data.get("pairing"), dict) else {}
    recog = data.get("recognition") if isinstance(data.get("recognition"), dict) else {}
    front = data.get("front")
    if not isinstance(front, str) or not front:
        return None
    back = data.get("back")
    if back is not None and not isinstance(back, str):
        back = None
    notes_text = data.get("notes")
    if not isinstance(notes_text, str):
        notes_text = ""
    raw = recog.get("raw_transcription")
    if not isinstance(raw, str):
        raw = ""
    engine = recog.get("engine")
    model = recog.get("model")
    reason = pairing.get("reason")
    related = pairing.get("related_reason")
    accepted = data.get("accepted_at")
    return ScanNotes(
        front=front,
        back=back,
        pairing_reason=reason if isinstance(reason, str) else "none",
        pairing_confirmed=pairing.get("confirmed") is True,
        engine=engine if isinstance(engine, str) else "",
        model=model if isinstance(model, str) else "",
        raw_transcription=raw[:MAX_TEXT],
        notes=notes_text[:MAX_TEXT],
        dates=parse_model_dates(data.get("dates")),
        accepted_at=accepted if isinstance(accepted, str) else "",
        size_similar=pairing.get("size_similar") is True,
        bits=load_bits(data.get("bits")),
        related_reason=related if isinstance(related, str) else "",
        name_suffix=_string_list(data.get("name_suffix")),
    )


def save_notes(path: Path, notes: ScanNotes) -> None:
    if not notes.accepted_at:
        notes.accepted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(dump_notes(notes), indent=2) + "\n", encoding="utf-8")


def notes_renames(done: list[tuple[Path, Path]]) -> list[tuple[Path, str]]:
    """Sidecar moves that follow image renames. Destination is a new file name."""
    pairs: list[tuple[Path, str]] = []
    for src, dst in done:
        sidecar = notes_path_for(src)
        if sidecar.exists():
            pairs.append((sidecar, notes_path_for(dst).name))
    return pairs


def rewrite_pairing_names(notes_file: Path, name_map: dict[str, str]) -> None:
    """Update stored front/back file names after a rename batch."""
    notes = load_notes(notes_file)
    if notes is None:
        return
    notes.front = name_map.get(notes.front, notes.front)
    if notes.back:
        notes.back = name_map.get(notes.back, notes.back)
    for item in notes.bits:
        item.source = name_map.get(item.source, item.source)
    save_notes(notes_file, notes)


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for item in raw[:24]:
        if isinstance(item, str) and item.strip():
            found.append(item.strip()[:40])
    return found
