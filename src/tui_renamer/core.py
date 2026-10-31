"""Rename rules, filename dates, sidecar JSON, and timezone search.

This module does not import Textual, Pillow, or subprocess. EXIF bytes live in
metadata.py. The UI renders the same step list this module uses to build a name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from difflib import SequenceMatcher
from pathlib import Path

try:
    from zoneinfo import ZoneInfo, available_timezones

    ZONEINFO_AVAILABLE = True
except ImportError:  # pragma: no cover - Python 3.9+ has zoneinfo
    ZoneInfo = None  # type: ignore[misc, assignment]
    available_timezones = None  # type: ignore[assignment]
    ZONEINFO_AVAILABLE = False

# A date, and optionally a time, in a filename.
# Groups: Y, M, D, [hh, mm, [ss]].
FILENAME_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)(?:[-_.:]?([0-5]\d))?)?(?!\d)"
)

EXCLUDED_TZ_PREFIXES = ("Etc/", "posix/", "right/", "SystemV/")


@dataclass
class RenameRules:
    """Toggles that build a new filename. source_tz None means the system zone."""

    add_datetime: bool = False
    add_modtime: bool = False
    add_origtime: bool = False
    use_json_data: bool = False
    add_geodata: bool = False
    use_prefix: bool = True
    strip_original_name: bool = False
    strip_to_text: bool = False
    remove_caps: bool = False
    remove_special: bool = False
    replace_spaces: bool = False
    replace_dots: bool = False
    source_tz: str | None = None
    datetime_stamp: str = ""


@dataclass(frozen=True)
class Step:
    """One active stage in the rename pipeline, drawn as a chip."""

    key: str
    label: str
    color: str


@dataclass(frozen=True)
class DateCandidate:
    """A clock the detail pane can show. text is already formatted."""

    label: str
    text: str


@dataclass
class FileFacts:
    """Everything the renamer knows about one file, after winner rules."""

    source: str = "file"
    origtime: datetime | None = None
    modtime: datetime | None = None
    origtime_text: str = ""
    modtime_text: str = ""
    geodata: str | None = None
    geodata_raw: dict[str, float] | None = None
    sidecar: Path | None = None
    candidates: list[DateCandidate] = field(default_factory=list)
    json_error: bool = False


@dataclass
class DatePlanItem:
    path: Path
    base: float
    prec: str
    cur: float
    target: float
    nudged: bool = False


@dataclass
class DatePlan:
    todo: list[DatePlanItem]
    same: list[DatePlanItem]
    skipped: list[str]


@dataclass
class RenameOutcome:
    done: list[tuple[Path, Path]]
    conflicts: int
    errors: list[str]


def reset_name_rules(rules: RenameRules) -> None:
    """Clear name toggles. The timezone override stays."""
    rules.add_datetime = False
    rules.add_modtime = False
    rules.add_origtime = False
    rules.use_json_data = False
    rules.add_geodata = False
    rules.use_prefix = True
    rules.strip_original_name = False
    rules.strip_to_text = False
    rules.remove_caps = False
    rules.remove_special = False
    rules.replace_spaces = False
    rules.replace_dots = False


def active_steps(rules: RenameRules) -> list[Step]:
    """Ordered pipeline. Only steps that are on are included.

    Order matches how a name is decided: source, zone, stem cleanup,
    then the time stamps, then geodata. The Prefix or Suffix chip says
    where the time stamps land in the finished name.
    """
    steps: list[Step] = []
    if rules.use_json_data:
        steps.append(Step("e", "JSON", "magenta"))
    if rules.source_tz:
        steps.append(Step("T", rules.source_tz, "cyan"))
    if rules.strip_original_name:
        steps.append(Step("x", "Strip", "red"))
    elif rules.strip_to_text:
        steps.append(Step("y", "Text", "yellow"))
    if rules.remove_caps:
        steps.append(Step("c", "lower", "green"))
    if rules.remove_special:
        steps.append(Step("s", "Symbols", "green"))
    if rules.replace_dots:
        steps.append(Step(".", "Dots", "green"))
    if rules.replace_spaces:
        steps.append(Step("p", "Dashes", "green"))
    times: list[Step] = []
    if rules.add_datetime:
        times.append(Step("d", "Date", "yellow"))
    if rules.add_modtime:
        times.append(Step("m", "Mod", "yellow"))
    if rules.add_origtime:
        times.append(Step("o", "Orig", "yellow"))
    if times:
        label = "Prefix" if rules.use_prefix else "Suffix"
        steps.append(Step("t", label, "cyan"))
        steps.extend(times)
    if rules.add_geodata:
        steps.append(Step("b", "Geo", "blue"))
    return steps


def list_files(directory: Path) -> list[Path]:
    """Top-level files, skipping dotfiles and JSON sidecars."""
    try:
        found = [
            path
            for path in directory.iterdir()
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith(".json")
        ]
    except OSError:
        return []
    return sorted(found, key=lambda path: path.name.lower())


def strip_existing_timestamps(basename: str) -> str:
    """Drop a YYYY-MM-DD-HHMMSS stamp glued to either end of the stem."""
    basename = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{6}-", "", basename)
    basename = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{6}$", "", basename)
    return basename


def clean_filename(basename: str, rules: RenameRules) -> str:
    name = basename
    if rules.remove_caps:
        name = name.lower()
    if rules.remove_special:
        name = re.sub(r"[^a-zA-Z0-9\-_\s.]", "", name)
    if rules.replace_dots:
        name = re.sub(r"\.", "-", name)
    if rules.replace_spaces:
        name = re.sub(r"[\s_]+", "-", name)
    return name.strip("-_")


def preview_name(filename: str, facts: FileFacts, rules: RenameRules) -> str:
    """Build the new filename. An empty result keeps the original name."""
    orig_base, ext = os.path.splitext(filename)
    if rules.strip_original_name:
        core = ""
    elif rules.strip_to_text:
        core = re.sub(r"[^a-zA-Z\s]", "", orig_base)
    else:
        core = orig_base
    core = clean_filename(strip_existing_timestamps(core), rules)
    time_prefix = ""
    time_suffix = ""
    if rules.use_prefix:
        if rules.add_datetime:
            time_prefix += f"{rules.datetime_stamp}-"
        if rules.add_modtime and facts.modtime_text:
            time_prefix += f"{facts.modtime_text}-"
        if rules.add_origtime and facts.origtime_text:
            time_prefix += f"{facts.origtime_text}-"
    else:
        if rules.add_datetime:
            time_suffix += f"-{rules.datetime_stamp}"
        if rules.add_modtime and facts.modtime_text:
            time_suffix += f"-{facts.modtime_text}"
        if rules.add_origtime and facts.origtime_text:
            time_suffix += f"-{facts.origtime_text}"
    geo_part = f"{facts.geodata}-" if rules.add_geodata and facts.geodata else ""
    new_base = re.sub(r"--+", "-", f"{time_prefix}{geo_part}{core}{time_suffix}").strip("-_")
    return (new_base + ext) if new_base else filename


def show_not_oldest_warning(facts: FileFacts, rules: RenameRules) -> bool:
    """True when the chosen stamp is newer than the other one."""
    if facts.modtime is None or facts.origtime is None:
        return False
    if rules.add_modtime and not rules.add_origtime and facts.modtime > facts.origtime:
        return True
    if rules.add_origtime and not rules.add_modtime and facts.origtime > facts.modtime:
        return True
    return False


def mtime_source(
    facts: FileFacts, rules: RenameRules, local_tz: tzinfo
) -> tuple[str, datetime | None]:
    """Clock used by set-mtime mode.

    Today's stamp is interpreted in the system zone, not the selected zone.
    That matches the string, which is formatted from the local clock.
    """
    if rules.add_datetime:
        try:
            parsed = datetime.strptime(rules.datetime_stamp, "%Y-%m-%d-%H%M%S")
        except ValueError:
            return "Invalid Date", None
        return rules.datetime_stamp, parsed.replace(tzinfo=local_tz)
    if rules.add_modtime and facts.modtime is not None:
        return facts.modtime_text, facts.modtime
    if rules.add_origtime and facts.origtime is not None:
        return facts.origtime_text, facts.origtime
    if facts.origtime is not None:
        return facts.origtime_text, facts.origtime
    return "N/A", None


def name_diff_ops(old: str, new: str) -> list[tuple[str, str]]:
    """Character diff as (equal|delete|insert, text) pairs."""
    if old == new:
        return [("equal", old)] if old else []
    ops: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=new).get_opcodes():
        if tag == "equal":
            ops.append(("equal", old[i1:i2]))
        elif tag == "delete":
            ops.append(("delete", old[i1:i2]))
        elif tag == "insert":
            ops.append(("insert", new[j1:j2]))
        else:
            if old[i1:i2]:
                ops.append(("delete", old[i1:i2]))
            if new[j1:j2]:
                ops.append(("insert", new[j1:j2]))
    return [(tag, text) for tag, text in ops if text]


def conflict_destination(source: Path, new_name: str) -> Path:
    """Pick a free path, adding -01, -02, ... before the extension."""
    target = source.parent / new_name
    if not target.exists():
        return target
    base, ext = os.path.splitext(new_name)
    suffix = 1
    while target.exists():
        target = source.parent / f"{base}-{suffix:02d}{ext}"
        suffix += 1
    return target


def rename_paths(pairs: list[tuple[Path, str]]) -> RenameOutcome:
    """Rename in the given order. Conflict suffixes are chosen at each step."""
    done: list[tuple[Path, Path]] = []
    conflicts = 0
    errors: list[str] = []
    for src, new_name in pairs:
        if not src.exists():
            errors.append(f"{src.name} is missing")
            continue
        if src.name == new_name:
            continue
        dst = conflict_destination(src, new_name)
        if dst.name != new_name:
            conflicts += 1
        try:
            src.rename(dst)
        except OSError as exc:
            errors.append(f"{src.name}: {exc}")
            continue
        done.append((src, dst))
    return RenameOutcome(done, conflicts, errors)


def parse_filename_datetime(name: str) -> tuple[datetime | None, str | None]:
    """Return a naive datetime and 'second', 'minute', or 'date'."""
    for match in FILENAME_DATE_RE.finditer(Path(name).stem):
        year, month, day, hour, minute, second = match.groups()
        try:
            if hour is None:
                return datetime(int(year), int(month), int(day)), "date"
            precision = "second" if second else "minute"
            return (
                datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0)),
                precision,
            )
        except ValueError:
            continue
    return None, None


def naive_to_ts(moment: datetime, source_tz: tzinfo | None) -> float:
    if source_tz is not None:
        return moment.replace(tzinfo=source_tz).timestamp()
    return moment.timestamp()


def plan_filename_dates(paths: list[Path], source_tz: tzinfo | None) -> DatePlan:
    """New timestamps for filename-date mode. Does not touch the files.

    A date with no time becomes 12:00, unless that calendar day already
    matches the file, in which case the existing clock is kept. Files that
    share a second are nudged forward by one second in stem order.
    """
    items: list[DatePlanItem] = []
    skipped: list[str] = []
    for path in paths:
        moment, precision = parse_filename_datetime(path.name)
        try:
            current = path.stat().st_mtime
        except OSError:
            current = None
        if moment is None or current is None:
            skipped.append(path.name)
            continue
        if precision == "date":
            current_day = datetime.fromtimestamp(current, tz=source_tz).date()
            if current_day == moment.date():
                base = current
                precision = "kept"
            else:
                base = naive_to_ts(moment.replace(hour=12), source_tz)
        else:
            base = naive_to_ts(moment, source_tz)
        items.append(
            DatePlanItem(
                path=path,
                base=base,
                prec=precision,
                cur=current,
                target=base,
                nudged=False,
            )
        )
    used: set[int] = set()
    ordered = sorted(
        (item for item in items if item.prec != "kept"),
        key=lambda item: (item.base, item.path.stem.lower()),
    )
    for item in ordered:
        tick = int(item.base)
        while tick in used:
            tick += 1
        used.add(tick)
        item.target = float(tick)
        item.nudged = tick != int(item.base)
    same = [item for item in items if abs(item.cur - item.target) < 1]
    same_ids = {id(item) for item in same}
    todo = [item for item in items if id(item) not in same_ids]
    return DatePlan(todo=todo, same=same, skipped=skipped)


def resolve_tz(name: str | None) -> tzinfo:
    """ZoneInfo for an IANA name. None and 'Local System' mean the local zone."""
    if not name or name == "Local System":
        local = datetime.now().astimezone().tzinfo
        assert local is not None
        return local
    if name == "UTC":
        return timezone.utc
    if not ZONEINFO_AVAILABLE or ZoneInfo is None:
        raise LookupError(name)
    return ZoneInfo(name)


def list_timezones() -> list[str]:
    if not ZONEINFO_AVAILABLE or available_timezones is None:
        return ["Local System", "UTC"]
    clean = sorted(
        name
        for name in available_timezones()
        if not name.startswith(EXCLUDED_TZ_PREFIXES) and "/" in name
    )
    return ["Local System", "UTC"] + clean


def filter_timezones(names: list[str], query: str) -> list[str]:
    """Keep zones whose name contains every whitespace-separated token."""
    tokens = query.lower().strip().split()
    if not tokens:
        return list(names)
    matches: list[str] = []
    for name in names:
        if name == "Local System":
            searchable = "local system default system local"
        elif name == "UTC":
            searchable = "utc gmt coordinated universal time"
        else:
            searchable = f"{name.lower().replace('_', ' ').replace('/', ' ')} {name.lower()}"
        if all(token in searchable for token in tokens):
            matches.append(name)
    return matches


def format_tz_display(name: str, now: datetime) -> str:
    """Label used in the timezone picker, including the current UTC offset."""
    if name == "Local System":
        try:
            offset = now.astimezone().strftime("UTC%z")
            if len(offset) >= 5:
                return f"Local System ({offset[:6]}:{offset[6:]}) [Default]"
        except Exception:
            pass
        return "Local System [Default]"
    if name == "UTC":
        return "UTC (UTC+00:00)"
    try:
        zone = resolve_tz(name)
        offset = now.astimezone(zone).strftime("UTC%z")
        if len(offset) >= 5:
            return f"{name} ({offset[:6]}:{offset[6:]})"
    except Exception:
        return name
    return name


def format_offset(moment: datetime) -> str:
    """EXIF offset, such as +05:30. Falls back to +00:00."""
    raw = moment.strftime("%z")
    if len(raw) >= 5:
        return f"{raw[:3]}:{raw[3:]}"
    return "+00:00"


def json_candidates(file_path: Path) -> list[Path]:
    """Sidecar paths, in the order the original script tried them."""
    filename = file_path.name
    stem = file_path.stem
    suffix = file_path.suffix
    raw = [
        file_path.with_suffix(suffix + ".json"),
        file_path.with_suffix(".json"),
        file_path.with_name(stem + ".json"),
        file_path.with_name(filename + ".json"),
        file_path.with_suffix(suffix.lower() + ".json"),
        file_path.with_name(stem.lower() + suffix.lower() + ".json"),
    ]
    truncated = re.sub(r"\(\d+\)$", "", stem)
    if truncated != stem:
        raw.append(file_path.with_name(truncated + suffix + ".json"))
        raw.append(file_path.with_name(truncated + suffix.lower() + ".json"))
    no_date = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{6}-", "", filename)
    if no_date != filename:
        raw.append(file_path.with_name(no_date + ".json"))
    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw:
        if candidate not in seen:
            seen.add(candidate)
            found.append(candidate)
    return found


def parse_json_time_field(time_dict: object) -> datetime | None:
    """Google Takeout time object, as a UTC datetime."""
    if not isinstance(time_dict, dict):
        return None
    timestamp = time_dict.get("timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    formatted = time_dict.get("formatted")
    if isinstance(formatted, str):
        try:
            piece = formatted.rsplit(" ", 1)[0].replace(",", "")
            parsed = datetime.strptime(piece, "%b %d %Y %I:%M:%S %p")
            return parsed.replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            return None
    return None


def parse_json_geodata(data: dict) -> tuple[str | None, dict[str, float] | None]:
    """Filename token and raw lat/lon. Latitude 0 falls through to geoDataExif."""
    geo_data = data.get("geoData")
    if geo_data is None or (isinstance(geo_data, dict) and geo_data.get("latitude") == 0.0):
        geo_data = data.get("geoDataExif", geo_data)
    if not isinstance(geo_data, dict):
        return None, None
    lat = geo_data.get("latitude")
    lon = geo_data.get("longitude")
    if lat is None or lon is None:
        return None, None
    try:
        lat_val = float(lat)
        lon_val = float(lon)
    except (TypeError, ValueError):
        return None, None
    if lat_val == 0.0 and lon_val == 0.0:
        return None, None
    lat_dir = "n" if lat_val >= 0 else "s"
    lon_dir = "e" if lon_val >= 0 else "w"
    label = f"geo-{abs(lat_val):.2f}{lat_dir}-{abs(lon_val):.2f}{lon_dir}"
    return label, {"latitude": lat_val, "longitude": lon_val}


def _show(moment: datetime, zone: tzinfo) -> str:
    if moment.tzinfo is None:
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    shown = moment.astimezone(zone)
    return f"{shown.strftime('%Y-%m-%d %H:%M:%S')} {format_offset(shown)}"


def _fs_times(path: Path) -> tuple[datetime | None, datetime | None]:
    try:
        stat = path.stat()
    except OSError:
        return None, None
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    birth_ts = getattr(stat, "st_birthtime", stat.st_ctime)
    birth = datetime.fromtimestamp(birth_ts, tz=timezone.utc)
    return modified, birth


def load_facts(
    path: Path,
    rules: RenameRules,
    zone: tzinfo,
    exif_naive: datetime | None = None,
    exif_offset: tzinfo | None = None,
    exif_label: str = "EXIF DateTimeOriginal",
    exif_offset_text: str = "",
) -> FileFacts:
    """Pick original and modified times the way the curses script did.

    JSON wins only while JSON mode is on. A JSON parse error hides EXIF and
    filesystem times. A missing offset uses the selected zone.
    """
    facts = FileFacts()
    filename_dt, filename_prec = parse_filename_datetime(path.name)
    if filename_dt is not None:
        note = filename_prec or "date"
        facts.candidates.append(
            DateCandidate("Filename", f"{filename_dt.strftime('%Y-%m-%d %H:%M:%S')} ({note})")
        )

    json_orig: datetime | None = None
    json_mod: datetime | None = None
    json_photo: datetime | None = None
    json_created: datetime | None = None
    if rules.use_json_data:
        sidecar = next((item for item in json_candidates(path) if item.exists()), None)
        facts.sidecar = sidecar
        if sidecar is not None:
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                facts.source = "json_err"
                facts.json_error = True
                return facts
            if not isinstance(data, dict):
                facts.source = "json_err"
                facts.json_error = True
                return facts
            json_photo = parse_json_time_field(data.get("photoTakenTime"))
            json_created = parse_json_time_field(data.get("creationTime"))
            json_orig = json_photo or json_created
            json_mod = parse_json_time_field(data.get("modificationTime"))
            facts.geodata, facts.geodata_raw = parse_json_geodata(data)
            if json_photo is not None:
                facts.candidates.append(
                    DateCandidate("JSON photoTakenTime", _show(json_photo, zone))
                )
            if json_created is not None:
                facts.candidates.append(
                    DateCandidate("JSON creationTime", _show(json_created, zone))
                )
            if json_mod is not None:
                facts.candidates.append(
                    DateCandidate("JSON modificationTime", _show(json_mod, zone))
                )
            if facts.geodata:
                facts.candidates.append(DateCandidate("Geodata", facts.geodata))

    exif_utc: datetime | None = None
    if exif_naive is not None:
        exif_zone = exif_offset or zone
        exif_utc = exif_naive.replace(tzinfo=exif_zone).astimezone(timezone.utc)
        label = exif_label
        if exif_offset_text:
            label = f"{exif_label} {exif_offset_text}"
        facts.candidates.append(DateCandidate(label, _show(exif_utc, zone)))

    fs_mod, fs_birth = _fs_times(path)
    if fs_mod is not None:
        facts.candidates.append(DateCandidate("mtime", _show(fs_mod, zone)))
    if fs_birth is not None:
        facts.candidates.append(DateCandidate("birth", _show(fs_birth, zone)))

    if rules.use_json_data and (json_orig or json_mod or facts.geodata):
        facts.source = "json"
    final_orig = (rules.use_json_data and json_orig) or exif_utc or fs_birth
    final_mod = (rules.use_json_data and json_mod) or fs_mod
    if final_orig:
        aware = final_orig.astimezone(zone)
        facts.origtime = aware
        facts.origtime_text = aware.strftime("%Y-%m-%d-%H%M%S")
    if final_mod:
        aware = final_mod.astimezone(zone)
        facts.modtime = aware
        facts.modtime_text = aware.strftime("%Y-%m-%d-%H%M%S")
    return facts


def datetime_for_write(path: Path, facts: FileFacts, zone: tzinfo) -> datetime | None:
    """Clock written by the timezone action.

    Original time, then modified time, then a date in the filename, then mtime.
    """
    if facts.origtime is not None:
        return facts.origtime.astimezone(zone)
    if facts.modtime is not None:
        return facts.modtime.astimezone(zone)
    filename_dt, _precision = parse_filename_datetime(path.name)
    if filename_dt is not None:
        return filename_dt.replace(tzinfo=zone)
    try:
        current = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(current, tz=zone)
