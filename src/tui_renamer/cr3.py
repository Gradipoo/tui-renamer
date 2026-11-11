"""Embedded JPEG preview inside a Canon CR3 file.

The PRVW tag stores a big-endian JPEG size, then the JPEG bytes. A preview that
does not start with a JPEG start-of-image marker is left unread. This module
does not write CR3 files.
"""

from __future__ import annotations

from pathlib import Path

_SOI = b"\xff\xd8"
_MAX_JPEG = 40 * 1024 * 1024


def embedded_preview_jpeg(path: Path) -> bytes | None:
    """JPEG bytes from the first PRVW tag that holds a JPEG. None otherwise."""
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    return preview_jpeg_bytes(blob)


def preview_jpeg_bytes(blob: bytes) -> bytes | None:
    """JPEG payload from a CR3 blob. None when no PRVW tag holds a JPEG."""
    start = 0
    while True:
        index = blob.find(b"PRVW", start)
        if index < 4:
            return None
        if index + 20 > len(blob):
            return None
        jpeg_size = int.from_bytes(blob[index + 16 : index + 20], "big")
        data_at = index + 20
        if jpeg_size < 2 or jpeg_size > _MAX_JPEG or data_at + jpeg_size > len(blob):
            start = index + 4
            continue
        payload = blob[data_at : data_at + jpeg_size]
        if payload.startswith(_SOI):
            return payload
        start = index + 4
