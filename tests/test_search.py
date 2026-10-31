"""Search matching, wrap, and middle truncation."""

from __future__ import annotations

from tui_renamer.core import fit_middle
from tui_renamer.search import (
    find_label,
    first_at_or_after,
    highlighted_name,
    kept_span,
    match_span,
    query_hits,
)


def test_smart_case_and_both_names() -> None:
    assert match_span("Cat.txt", "cat") == (0, 3)
    assert match_span("Cat.txt", "Cat") == (0, 3)
    assert match_span("cat.txt", "Cat") is None
    names = [("a.txt", "a.txt"), ("b.txt", "b-cat.txt"), ("Cat.txt", "Cat.txt")]
    assert query_hits(names, "cat") == [1, 2]
    assert query_hits(names, "Cat") == [2]
    assert query_hits(names, "") == []


def test_wrap_and_label() -> None:
    hits = [0, 2, 5]
    assert first_at_or_after(2, hits) == 2
    assert first_at_or_after(3, hits) == 5
    assert first_at_or_after(6, hits) == 0
    assert find_label(2, hits) == "find 2/3"
    assert find_label(1, hits) == "find 0/3"
    assert find_label(0, []) == "find 0/0"


def test_kept_span_matches_fit_middle() -> None:
    text = "abcdefghij"
    assert fit_middle(text, 6) == "a...ij"
    assert kept_span(text, 6, 0, 1) == (0, 1)
    assert kept_span(text, 6, 8, 10) == (4, 6)
    assert kept_span(text, 6, 2, 3) is None
    assert kept_span("short", 20, 1, 4) == (1, 4)


def test_status_name_prefers_the_current_name() -> None:
    current = highlighted_name("cat.txt", "cat-new.txt", "cat")
    assert current is not None
    assert current.plain == "cat.txt"
    assert current.spans
    new_only = highlighted_name("a.txt", "a-cat.txt", "cat")
    assert new_only is not None
    assert new_only.plain == "a-cat.txt"
    assert highlighted_name("a.txt", "b.txt", "cat") is None
