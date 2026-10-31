"""Read and write EXIF, QuickTime dates, GPS, and birth time.

JPEG and TIFF dates go through piexif. Video, HEIC, and PNG go through exiftool
when it is on PATH. Birth time uses SetFile on macOS and SetFileTime on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from tui_renamer.core import FileFacts, RenameRules, format_offset, load_facts

try:
    from PIL import Image

    PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - installed with the package
    Image = None  # type: ignore[misc, assignment]
    PILLOW_AVAILABLE = False

try:
    import piexif

    PIEXIF_AVAILABLE = True
except ImportError:  # pragma: no cover - installed with the package
    piexif = None  # type: ignore[assignment]
    PIEXIF_AVAILABLE = False

EXIF_IFD = 0x8769
OFFSET_TIME = 36880
OFFSET_TIME_ORIGINAL = 36881
OFFSET_TIME_DIGITIZED = 36882
DATETIME_ORIGINAL = 36867
DATETIME = 306

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".tif", ".tiff"})
VIDEO_EXTENSIONS = frozenset({".mov", ".mp4", ".m4v"})
EXIFTOOL_STILL_EXTENSIONS = frozenset({".heic", ".heif", ".png", ".jpg", ".jpeg"})

CommandRunner = Callable[[list[str]], bool]


@dataclass(frozen=True)
class ExifClock:
    naive: datetime | None
    offset: tzinfo | None
    label: str
    offset_text: str


@dataclass
class WriteCounts:
    filesystem_ok: int = 0
    metadata_ok: int = 0
    metadata_failed: int = 0
    skipped: int = 0


def parse_offset(offset_str: str) -> timezone | None:
    """Parse an EXIF offset such as +05:30 or -08:00."""
    if not isinstance(offset_str, str) or ":" not in offset_str:
        return None
    try:
        sign = -1 if offset_str.startswith("-") else 1
        parts = offset_str.replace("+", "-").split("-")[-1].split(":")
        hours, minutes = int(parts[0]), int(parts[1])
        return timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
    except (ValueError, IndexError):
        return None


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if not isinstance(value, str):
        return None
    cleaned = value.strip("\x00").strip()
    return cleaned or None


def read_exif(path: Path) -> ExifClock:
    """Date and offset from the Exif IFD.

    DateTimeOriginal (36867) wins over DateTime (306). The offset is
    OffsetTimeOriginal (36881), then OffsetTime (36880). Tag 36882 is
    written with the others and is not used as a fallback.
    """
    empty = ExifClock(None, None, "", "")
    if path.suffix.lower() not in IMAGE_EXTENSIONS or not PILLOW_AVAILABLE or Image is None:
        return empty
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            ifd = exif.get_ifd(EXIF_IFD)
            base = dict(exif)
    except Exception:
        return empty
    original = _text(ifd.get(DATETIME_ORIGINAL))
    fallback = _text(base.get(DATETIME)) or _text(ifd.get(DATETIME))
    text = original or fallback
    naive = None
    if text:
        try:
            naive = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            naive = None
    offset_text = _text(ifd.get(OFFSET_TIME_ORIGINAL)) or _text(ifd.get(OFFSET_TIME)) or ""
    offset = parse_offset(offset_text) if offset_text else None
    if naive is None:
        return ExifClock(None, offset, "", offset_text)
    label = "EXIF DateTimeOriginal" if original else "EXIF DateTime"
    return ExifClock(naive, offset, label, offset_text)


def inspect_file(path: Path, rules: RenameRules, zone: tzinfo) -> FileFacts:
    clock = read_exif(path)
    return load_facts(
        path,
        rules,
        zone,
        exif_naive=clock.naive,
        exif_offset=clock.offset,
        exif_label=clock.label or "EXIF DateTimeOriginal",
        exif_offset_text=clock.offset_text,
    )


def write_image_exif_datetime(path: Path, moment: datetime) -> bool:
    """Write local date and the three EXIF 2.31 offset tags into JPEG or TIFF."""
    if not PIEXIF_AVAILABLE or piexif is None:
        return False
    date_str = moment.strftime("%Y:%m:%d %H:%M:%S")
    offset_str = format_offset(moment)
    try:
        exif_dict = piexif.load(str(path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}, "thumbnail": None}
    if not isinstance(exif_dict.get("0th"), dict):
        exif_dict["0th"] = {}
    if not isinstance(exif_dict.get("Exif"), dict):
        exif_dict["Exif"] = {}
    exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str
    exif_dict["Exif"][OFFSET_TIME] = offset_str
    exif_dict["Exif"][OFFSET_TIME_ORIGINAL] = offset_str
    exif_dict["Exif"][OFFSET_TIME_DIGITIZED] = offset_str
    try:
        piexif.insert(piexif.dump(exif_dict), str(path))
        return True
    except Exception:
        try:
            clean = {
                "0th": {piexif.ImageIFD.DateTime: date_str},
                "Exif": {
                    piexif.ExifIFD.DateTimeOriginal: date_str,
                    piexif.ExifIFD.DateTimeDigitized: date_str,
                    OFFSET_TIME: offset_str,
                    OFFSET_TIME_ORIGINAL: offset_str,
                    OFFSET_TIME_DIGITIZED: offset_str,
                },
                "GPS": exif_dict.get("GPS") or {},
                "1st": {},
                "thumbnail": None,
            }
            piexif.insert(piexif.dump(clean), str(path))
            return True
        except Exception:
            return False


def video_exiftool_command(exiftool: str, path: Path, moment: datetime) -> list[str]:
    date_str = moment.strftime("%Y:%m:%d %H:%M:%S")
    offset_str = format_offset(moment)
    utc_str = moment.astimezone(timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
    return [
        exiftool,
        "-overwrite_original",
        f"-QuickTime:CreationDate={date_str}{offset_str}",
        f"-QuickTime:CreateDate={utc_str}",
        f"-QuickTime:ModifyDate={utc_str}",
        str(path),
    ]


def generic_exiftool_command(exiftool: str, path: Path, moment: datetime) -> list[str]:
    date_str = moment.strftime("%Y:%m:%d %H:%M:%S")
    offset_str = format_offset(moment)
    return [
        exiftool,
        "-overwrite_original",
        f"-AllDates={date_str}",
        f"-OffsetTime={offset_str}",
        f"-OffsetTimeOriginal={offset_str}",
        f"-OffsetTimeDigitized={offset_str}",
        str(path),
    ]


def run_command(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return result.returncode == 0


def write_file_metadata_and_offset(
    path: Path,
    moment: datetime,
    *,
    exiftool: str | None,
    runner: CommandRunner | None = None,
) -> bool:
    ext = path.suffix.lower()
    run = runner or run_command
    if PIEXIF_AVAILABLE and ext in IMAGE_EXTENSIONS:
        return write_image_exif_datetime(path, moment)
    if exiftool and ext in VIDEO_EXTENSIONS:
        return run(video_exiftool_command(exiftool, path, moment))
    if exiftool and ext in EXIFTOOL_STILL_EXTENSIONS:
        return run(generic_exiftool_command(exiftool, path, moment))
    return False


def degrees_to_dms_rational(
    degrees: float,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    absolute = abs(degrees)
    minutes, seconds = divmod(absolute * 3600, 60)
    whole, minutes = divmod(minutes, 60)
    return ((int(whole), 1), (int(minutes), 1), (int(seconds * 10000), 10000))


def write_geodata(path: Path, latitude: float, longitude: float) -> bool:
    """Write GPS into a JPEG or TIFF. Other types are left alone."""
    if not PIEXIF_AVAILABLE or piexif is None or path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    try:
        exif_dict = piexif.load(str(path))
        gps = exif_dict.get("GPS")
        if not isinstance(gps, dict):
            gps = {}
            exif_dict["GPS"] = gps
        gps[piexif.GPSIFD.GPSLatitudeRef] = b"N" if latitude >= 0 else b"S"
        gps[piexif.GPSIFD.GPSLatitude] = degrees_to_dms_rational(latitude)
        gps[piexif.GPSIFD.GPSLongitudeRef] = b"E" if longitude >= 0 else b"W"
        gps[piexif.GPSIFD.GPSLongitude] = degrees_to_dms_rational(longitude)
        piexif.insert(piexif.dump(exif_dict), str(path))
        return True
    except Exception:
        return False


def find_exiftool() -> str | None:
    return shutil.which("exiftool")


def find_setfile() -> str | None:
    found = shutil.which("SetFile")
    if found:
        return found
    fallback = Path("/usr/bin/SetFile")
    if fallback.exists():
        return str(fallback)
    return None


def setfile_command(setfile: str, path: Path, moment: datetime) -> list[str]:
    date_str = moment.strftime("%m/%d/%Y %H:%M:%S")
    return [setfile, "-d", date_str, "-m", date_str, str(path)]


def windows_filetime(timestamp: float) -> tuple[int, int]:
    """FILETIME low and high dwords for a POSIX timestamp."""
    ticks = int((timestamp + 11644473600) * 10_000_000)
    return ticks & 0xFFFFFFFF, ticks >> 32


def _set_creation_time_windows(path: Path, timestamp: float) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x0100, 0, None, 3, 0x80, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        return False
    try:
        low, high = windows_filetime(timestamp)
        created = wintypes.FILETIME(low, high)
        if not kernel.SetFileTime(handle, ctypes.byref(created), None, None):
            return False
        return True
    finally:
        kernel.CloseHandle(handle)


def set_birthtime(
    path: Path,
    moment: datetime,
    *,
    setfile: str | None,
    runner: CommandRunner | None = None,
) -> bool:
    """Set creation time. Linux has no portable call, so this returns False there."""
    if os.name == "nt":
        return _set_creation_time_windows(path, moment.timestamp())
    if sys.platform == "darwin" and setfile:
        return (runner or run_command)(setfile_command(setfile, path, moment))
    return False


def set_filesystem_times(
    path: Path,
    moment: datetime,
    *,
    setfile: str | None,
    runner: CommandRunner | None = None,
) -> bool:
    """Set atime and mtime. Birth time is best effort and does not fail the call."""
    try:
        stamp = moment.timestamp()
        os.utime(path, (stamp, stamp))
    except OSError:
        return False
    try:
        set_birthtime(path, moment, setfile=setfile, runner=runner)
    except Exception:
        pass
    return True


def can_write_metadata(path: Path) -> bool:
    """True when this suffix has a piexif or exiftool writer."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS:
        return True
    return ext in EXIFTOOL_STILL_EXTENSIONS


def tally_write(
    counts: WriteCounts,
    *,
    filesystem_ok: bool,
    metadata_ok: bool | None,
) -> None:
    """Count filesystem updates and metadata writes separately.

    metadata_ok None means this file has no metadata writer, so it is not a failure.
    """
    if filesystem_ok:
        counts.filesystem_ok += 1
    if metadata_ok is True:
        counts.metadata_ok += 1
    elif metadata_ok is False:
        counts.metadata_failed += 1


def timezone_write_message(counts: WriteCounts) -> str:
    parts = [
        f"Updated filesystem times on {counts.filesystem_ok} files.",
        f"Wrote metadata on {counts.metadata_ok}.",
    ]
    if counts.metadata_failed == 1:
        parts.append("1 metadata write failed.")
    elif counts.metadata_failed:
        parts.append(f"{counts.metadata_failed} metadata writes failed.")
    if counts.skipped == 1:
        parts.append("1 file had no time.")
    elif counts.skipped:
        parts.append(f"{counts.skipped} files had no time.")
    return " ".join(parts)


def snapshot_mode(path: Path) -> str:
    """How to remember a file before a metadata write: piexif, copy, or times."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "piexif"
    if ext in VIDEO_EXTENSIONS or ext in {".heic", ".heif", ".png"}:
        return "copy"
    return "times"
