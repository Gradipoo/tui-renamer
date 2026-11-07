"""Related-file candidates for the current photo.

Tiers are deterministic. Size never makes a Likely match. Unknown is not
the same as disproven.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.style import Style
from rich.text import Text

from tui_renamer.scan_pairing import (
    BACK_TAGS,
    FRONT_TAGS,
    filename_tag,
    scan_images,
    sizes_similar,
)

TIER_ORDER = {"likely": 0, "possible": 1, "unknown": 2, "unlikely": 3}
TIER_STYLE = {
    "likely": Style(color="green"),
    "possible": Style(color="yellow"),
    "unknown": Style(color="yellow"),
    "unlikely": Style(color="red"),
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    tier: str
    label: str
    reason: str
    size_similar: bool = False

    def line(self) -> Text:
        style = TIER_STYLE.get(self.tier, Style())
        text = Text()
        text.append(f"[{self.label}] ", style)
        text.append(self.path.name)
        text.append(f"  {self.reason}", Style(dim=True))
        return text


def score_candidates(subject: Path, files: list[Path]) -> list[Candidate]:
    images = scan_images(files)
    others = [path for path in images if path != subject]
    unique: Path | None = None
    ambiguous: set[Path] = set()
    tagged = filename_tag(subject)
    if tagged is not None:
        base, tag = tagged
        partners: list[Path] = []
        for path in others:
            other = filename_tag(path)
            if other is None or other[0] != base:
                continue
            if tag in FRONT_TAGS and other[1] in BACK_TAGS:
                partners.append(path)
            elif tag in BACK_TAGS and other[1] in FRONT_TAGS:
                partners.append(path)
        if len(partners) == 1:
            unique = partners[0]
        elif len(partners) > 1:
            ambiguous = set(partners)
    neighbors: set[Path] = set()
    if subject in images:
        index = images.index(subject)
        if index > 0:
            neighbors.add(images[index - 1])
        if index + 1 < len(images):
            neighbors.add(images[index + 1])
    rows: list[Candidate] = []
    for path in others:
        similar = sizes_similar(subject, path)
        if path == unique:
            rows.append(Candidate(path, "likely", "Likely", "filename partner", similar))
        elif path in ambiguous:
            rows.append(
                Candidate(
                    path,
                    "possible",
                    "Possible",
                    "filename tag is ambiguous",
                    similar,
                )
            )
        elif path in neighbors:
            reason = "next file in folder"
            if similar:
                reason += ", similar size (not enough for Likely)"
            rows.append(Candidate(path, "possible", "Possible", reason, similar))
        elif similar:
            rows.append(
                Candidate(
                    path,
                    "unknown",
                    "Unknown",
                    "similar size only, not evidence enough",
                    True,
                )
            )
        else:
            rows.append(
                Candidate(
                    path,
                    "unlikely",
                    "Unlikely",
                    "no name or order link; lack of evidence is not disproven",
                    False,
                )
            )
    rows.sort(key=lambda item: (TIER_ORDER.get(item.tier, 9), item.path.name.casefold()))
    return rows
