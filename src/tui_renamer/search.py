"""Find text in the current name and the new name."""

from __future__ import annotations

from rich.text import Text


def is_sensitive(query: str) -> bool:
    """Uppercase in the query turns on case sensitive matching."""
    return any(char.isupper() for char in query)


def match_span(text: str, query: str) -> tuple[int, int] | None:
    """Return the first match as a half open span, or None."""
    if not query:
        return None
    if is_sensitive(query):
        found = text.find(query)
        if found < 0:
            return None
        return found, found + len(query)
    haystack = text.casefold()
    needle = query.casefold()
    found = haystack.find(needle)
    if found < 0:
        return None
    if len(haystack) == len(text):
        return found, found + len(needle)
    return found, min(len(text), found + len(needle))


def query_hits(names: list[tuple[str, str]], query: str) -> list[int]:
    """Indexes whose current name or new name contains the query."""
    if not query:
        return []
    hits: list[int] = []
    for index, (current, new) in enumerate(names):
        if match_span(current, query) or match_span(new, query):
            hits.append(index)
    return hits


def first_at_or_after(origin: int, hits: list[int]) -> int:
    """First hit at or after origin. Wrap once to the first hit."""
    for hit in hits:
        if hit >= origin:
            return hit
    return hits[0]


def find_label(cursor: int, hits: list[int]) -> str:
    """Header text such as `find 3/17`. `find 0/0` when nothing matches."""
    if not hits:
        return "find 0/0"
    try:
        place = hits.index(cursor) + 1
    except ValueError:
        place = 0
    return f"find {place}/{len(hits)}"


def kept_span(text: str, width: int, start: int, end: int) -> tuple[int, int] | None:
    """Map a span onto `fit_middle` output when the whole span is still visible."""
    if width <= 0 or start < 0 or end > len(text) or start >= end:
        return None
    if len(text) <= width:
        return start, end
    if width < 5:
        if end <= width:
            return start, end
        return None
    head = (width - 3) // 2
    tail = width - 3 - head
    if end <= head:
        return start, end
    tail_start = len(text) - tail
    if start >= tail_start:
        offset = head + 3
        return offset + (start - tail_start), offset + (end - tail_start)
    return None


def highlighted_name(current: str, new: str, query: str) -> Text | None:
    """Full matching name, current first, with the match reversed."""
    span = match_span(current, query)
    if span is not None:
        return _reversed(current, span)
    span = match_span(new, query)
    if span is not None:
        return _reversed(new, span)
    return None


def _reversed(text: str, span: tuple[int, int]) -> Text:
    shown = Text(text)
    shown.stylize("reverse", span[0], span[1])
    return shown
