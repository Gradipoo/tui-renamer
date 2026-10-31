"""Read a capture time from a QuickTime or MP4 file.

No extra packages. The walker reads `moov` only, and stops at 8MB.
`com.apple.quicktime.creationdate` wins when the string has a timezone.
Otherwise the `mvhd` creation time is used, counted from 1904-01-01 UTC.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

MOOV_CAP = 8 * 1024 * 1024
QT_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
CREATION_KEY = "com.apple.quicktime.creationdate"
_CONTAINERS = frozenset({b"udta"})


@dataclass(frozen=True)
class QuickTimeClock:
    naive: datetime | None
    offset: tzinfo | None
    label: str
    offset_text: str


def read_quicktime(path: Path) -> QuickTimeClock:
    """Capture clock from `moov`, or an empty clock when the file has none."""
    empty = QuickTimeClock(None, None, "", "")
    try:
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            while offset + 8 <= file_size:
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    break
                box_size, box_type, header_len = _header(header, file_size - offset)
                if box_size is None or box_type is None or header_len is None:
                    break
                if offset + box_size > file_size:
                    break
                if box_type == b"moov":
                    handle.seek(offset + header_len)
                    payload = handle.read(min(box_size - header_len, MOOV_CAP))
                    return _clock_from_moov(payload)
                offset += box_size
    except OSError:
        return empty
    return empty


def parse_creationdate(text: str) -> QuickTimeClock | None:
    """ISO creation date that includes a timezone. Other strings are ignored."""
    parsed = _split_iso(text.strip())
    if parsed is None:
        return None
    naive, offset, offset_text = parsed
    if offset is None:
        return None
    return QuickTimeClock(naive, offset, "QuickTime creation date", offset_text)


def _clock_from_moov(data: bytes) -> QuickTimeClock:
    found = _Found()
    _scan(data, 0, len(data), found, 0)
    if found.creation:
        parsed = parse_creationdate(found.creation)
        if parsed is not None:
            return parsed
    if found.mvhd is not None:
        return QuickTimeClock(found.mvhd, timezone.utc, "QuickTime mvhd", "+00:00")
    return QuickTimeClock(None, None, "", "")


class _Found:
    def __init__(self) -> None:
        self.mvhd: datetime | None = None
        self.creation: str | None = None


def _scan(data: bytes, start: int, end: int, found: _Found, depth: int) -> None:
    if depth > 6:
        return
    for box_type, payload_start, payload_end in _iter_boxes(data, start, end):
        if box_type == b"mvhd" and found.mvhd is None:
            found.mvhd = _mvhd_time(data, payload_start, payload_end)
        elif box_type == b"meta":
            text = _creation_in_meta(data, payload_start, payload_end)
            if text and found.creation is None:
                found.creation = text
        elif box_type in _CONTAINERS:
            _scan(data, payload_start, payload_end, found, depth + 1)


def _creation_in_meta(data: bytes, start: int, end: int) -> str | None:
    """`meta` is a full box: skip the version and flags, then read keys and ilst."""
    child = start + 4
    if child > end:
        return None
    names: list[str] = []
    for box_type, payload_start, payload_end in _iter_boxes(data, child, end):
        if box_type == b"keys":
            names = _key_names(data, payload_start, payload_end)
    if CREATION_KEY not in names:
        return None
    index = names.index(CREATION_KEY) + 1
    for box_type, payload_start, payload_end in _iter_boxes(data, child, end):
        if box_type != b"ilst":
            continue
        text = _ilst_text(data, payload_start, payload_end, index)
        if text:
            return text
    return None


def _key_names(data: bytes, start: int, end: int) -> list[str]:
    """`keys` is a full box, then a count, then size-prefixed mdta names."""
    pos = start + 4
    if pos + 4 > end:
        return []
    count = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    names: list[str] = []
    for _ in range(min(count, 256)):
        if pos + 8 > end:
            break
        key_size = struct.unpack_from(">I", data, pos)[0]
        if key_size < 8 or pos + key_size > end:
            break
        namespace = data[pos + 4 : pos + 8]
        raw = data[pos + 8 : pos + key_size]
        if namespace == b"mdta":
            names.append(raw.decode("utf-8", "replace"))
        else:
            names.append("")
        pos += key_size
    return names


def _ilst_text(data: bytes, start: int, end: int, index: int) -> str | None:
    wanted = index.to_bytes(4, "big")
    for box_type, payload_start, payload_end in _iter_boxes(data, start, end):
        if box_type != wanted:
            continue
        for child_type, child_start, child_end in _iter_boxes(data, payload_start, payload_end):
            if child_type != b"data" or child_end - child_start < 8:
                continue
            kind = struct.unpack_from(">I", data, child_start)[0]
            if kind != 1:
                continue
            raw = data[child_start + 8 : child_end].split(b"\x00", 1)[0]
            text = raw.decode("utf-8", "replace").strip()
            if text:
                return text
    return None


def _mvhd_time(data: bytes, start: int, end: int) -> datetime | None:
    if end - start < 8:
        return None
    version = data[start]
    if version == 0:
        if end - start < 8:
            return None
        seconds = struct.unpack_from(">I", data, start + 4)[0]
    elif version == 1:
        if end - start < 12:
            return None
        seconds = struct.unpack_from(">Q", data, start + 4)[0]
    else:
        return None
    if seconds == 0 or seconds == 0xFFFFFFFF:
        return None
    try:
        moment = QT_EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        return None
    if moment.year < 1970 or moment.year > 2100:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _header(header: bytes, remaining: int) -> tuple[int | None, bytes | None, int | None]:
    box_size, box_type = struct.unpack_from(">I4s", header)
    header_len = 8
    if box_size == 1:
        if len(header) < 16:
            return None, None, None
        box_size = struct.unpack_from(">Q", header, 8)[0]
        header_len = 16
    elif box_size == 0:
        box_size = remaining
    if box_size < header_len:
        return None, None, None
    return box_size, box_type, header_len


def _iter_boxes(data: bytes, start: int, end: int) -> list[tuple[bytes, int, int]]:
    boxes: list[tuple[bytes, int, int]] = []
    pos = start
    while pos + 8 <= end:
        box_size, box_type = struct.unpack_from(">I4s", data, pos)
        header_len = 8
        if box_size == 1:
            if pos + 16 > end:
                break
            box_size = struct.unpack_from(">Q", data, pos + 8)[0]
            header_len = 16
        elif box_size == 0:
            box_size = end - pos
        if box_size < header_len or pos + box_size > end:
            break
        boxes.append((box_type, pos + header_len, pos + box_size))
        pos += box_size
    return boxes


def _split_iso(text: str) -> tuple[datetime, tzinfo | None, str] | None:
    if len(text) < 19:
        return None
    if text[4] != "-" or text[7] != "-" or text[10] not in "T ":
        return None
    if text[13] != ":" or text[16] != ":":
        return None
    try:
        naive = datetime(
            int(text[0:4]),
            int(text[5:7]),
            int(text[8:10]),
            int(text[11:13]),
            int(text[14:16]),
            int(text[17:19]),
        )
    except ValueError:
        return None
    rest = text[19:]
    if rest.startswith("."):
        index = 1
        while index < len(rest) and rest[index].isdigit():
            index += 1
        if index == 1:
            return None
        rest = rest[index:]
    if not rest:
        return naive, None, ""
    offset, offset_text = _offset(rest)
    if offset is None:
        return None
    return naive, offset, offset_text


def _offset(text: str) -> tuple[tzinfo | None, str]:
    if text == "Z":
        return timezone.utc, "+00:00"
    if len(text) < 5 or text[0] not in "+-":
        return None, ""
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    if len(digits) not in {2, 4} or not digits.isdigit():
        return None, ""
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) == 4 else 0
    if hours > 23 or minutes > 59:
        return None, ""
    zone = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
    return zone, f"{text[0]}{hours:02d}:{minutes:02d}"
