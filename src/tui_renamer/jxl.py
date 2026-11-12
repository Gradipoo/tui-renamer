"""Top-level boxes in a JPEG XL container.

The container starts with the 12-byte JXL signature. An uncompressed Exif box
holds a TIFF blob. jbrd marks a lossless JPEG reconstruction. A brob box is
brotli-compressed and is skipped here, with no extra dependency. This module
does not decode pixels and does not write files.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

_SIGNATURE = bytes.fromhex("0000000c4a584c200d0a870a")
_TIFF_HEADERS = (b"II*\x00", b"MM\x00*")
_EXIF_CAP = 8 * 1024 * 1024


@dataclass(frozen=True)
class JxlLayout:
    """Exif TIFF payload, and whether a top-level jbrd box is present."""

    exif: bytes | None = None
    has_jbrd: bool = False


def read_jxl_layout(path: Path) -> JxlLayout:
    """Walk top-level boxes. An empty layout means there is no container."""
    empty = JxlLayout()
    try:
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            if handle.read(len(_SIGNATURE)) != _SIGNATURE:
                return empty
            offset = len(_SIGNATURE)
            exif: bytes | None = None
            has_jbrd = False
            while offset + 8 <= file_size:
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    break
                box_size, box_type, header_len = _box_header(header, file_size - offset)
                if box_size is None or box_type is None or header_len is None:
                    break
                if offset + box_size > file_size:
                    break
                if box_type == b"Exif" and exif is None:
                    payload_len = box_size - header_len
                    if payload_len <= _EXIF_CAP:
                        handle.seek(offset + header_len)
                        tiff = _tiff_from_exif(handle.read(payload_len))
                        if tiff is not None:
                            exif = tiff
                elif box_type == b"jbrd":
                    has_jbrd = True
                offset += box_size
                if exif is not None and has_jbrd:
                    break
            return JxlLayout(exif, has_jbrd)
    except OSError:
        return empty


def _tiff_from_exif(payload: bytes) -> bytes | None:
    """TIFF bytes from an Exif box. Drop a leading offset when bytes 4:8 are TIFF."""
    if len(payload) >= 8 and payload[4:8] in _TIFF_HEADERS:
        payload = payload[4:]
    if len(payload) < 8:
        return None
    return payload


def _box_header(header: bytes, remaining: int) -> tuple[int | None, bytes | None, int | None]:
    """Box size, type, and header length. size 1 is largesize. size 0 runs to EOF."""
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
