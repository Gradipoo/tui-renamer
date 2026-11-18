"""Plan reconciliation of dates from filenames and file facts."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from tui_renamer.core import (
    FileFacts,
    earliest_date,
    earliest_source,
    filename_clock_text,
    filename_date_match,
)


@dataclass(frozen=True)
class ReconcileCandidate:
    label: str
    moment: datetime
    precision: str
    warning: str = ""
    capture: bool = False
    blocked: bool = False
    assigned: bool = False


@dataclass(frozen=True)
class Reconciliation:
    name: str
    filename: ReconcileCandidate | None
    earliest: ReconcileCandidate | None
    recommended: ReconcileCandidate | None
    candidates: tuple[ReconcileCandidate, ...]
    stamp_span: tuple[int, int] | None

    def proposed_name(self, candidate: ReconcileCandidate, *, use_prefix: bool = True) -> str:
        if candidate.blocked or candidate.precision != "second":
            return self.name
        stamp = filename_clock_text(candidate.moment)
        if self.stamp_span is not None:
            start, end = self.stamp_span
            return f"{self.name[:start]}{stamp}{self.name[end:]}"
        stem, suffix = Path(self.name).stem, Path(self.name).suffix
        return f"{stamp}-{stem}{suffix}" if use_prefix else f"{stem}-{stamp}{suffix}"


def _clock_warning(wall: datetime, zone: tzinfo) -> str:
    instants = set()
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            instants.add(instant)
    if not instants:
        return "This local time does not exist in the selected timezone."
    if len(instants) > 1:
        return "This local time occurs twice in the selected timezone."
    return ""


def _candidate(label: str, moment: datetime, precision: str, capture: bool) -> ReconcileCandidate:
    warning = _clock_warning(moment.replace(tzinfo=None, microsecond=0), moment.tzinfo)
    blocked = bool(warning) or precision not in {"date", "minute", "second"}
    if not warning:
        warning = {
            "date": "Date only. The file time uses 12:00.",
            "minute": "Minute only. The file time uses zero seconds.",
            "second": "",
        }.get(precision, "The date precision is uncertain.")
    return ReconcileCandidate(
        label,
        moment,
        precision,
        warning,
        capture and precision == "second" and not blocked,
        blocked,
    )


def _bounds(candidate: ReconcileCandidate) -> tuple[datetime, datetime]:
    start = candidate.moment
    if candidate.precision == "date":
        start = start.replace(hour=0)
        end = start + timedelta(days=1)
    elif candidate.precision == "minute":
        end = start + timedelta(minutes=1)
    else:
        end = start
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _recommended(
    filename: ReconcileCandidate | None, earliest: ReconcileCandidate | None
) -> ReconcileCandidate | None:
    if filename is None or earliest is None:
        candidate = filename or earliest
        return candidate if candidate is not None and not candidate.blocked else None
    if filename.blocked or earliest.blocked:
        return None
    filename_start, filename_end = _bounds(filename)
    earliest_start, earliest_end = _bounds(earliest)
    if filename_end <= earliest_start:
        return filename
    if earliest_end < filename_start or (
        earliest_end == filename_start and earliest.precision != "second"
    ):
        return earliest
    return None


def reconcile_dates(name: str, facts: FileFacts, zone: tzinfo) -> Reconciliation:
    """Compare the actual filename's date to the earliest eligible file fact."""
    wall, precision, span = filename_date_match(name)
    filename = None
    if wall is not None and precision is not None:
        if precision == "date":
            wall = wall.replace(hour=12)
        filename = _candidate("Filename", wall.replace(tzinfo=zone), precision, capture=True)

    source = earliest_date(facts)
    alternatives = [
        fact
        for fact in facts.candidates
        if fact.instant is not None and fact.label not in {"Filename", "Geodata"}
    ]
    if source is not None and source not in alternatives:
        alternatives.append(source)
    options = {
        fact: _candidate(
            earliest_source(fact), fact.instant.astimezone(zone), fact.precision, fact.capture
        )
        for fact in alternatives
    }
    earliest = options.get(source)
    recommended = _recommended(filename, earliest)
    candidates = dict.fromkeys((recommended, filename, earliest, *options.values()))
    candidates.pop(None, None)
    return Reconciliation(name, filename, earliest, recommended, tuple(candidates), span)


def assigned_reconcile_candidate(candidate: ReconcileCandidate, zone: tzinfo) -> ReconcileCandidate:
    """Keep the selected instant and recheck its clock in the current display zone."""
    label = candidate.label if candidate.assigned else f"Chosen · {candidate.label}"
    chosen = _candidate(
        label, candidate.moment.astimezone(zone), candidate.precision, candidate.capture
    )
    return replace(chosen, assigned=True)
