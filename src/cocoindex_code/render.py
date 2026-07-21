"""Shared terminal rendering for the local searches — ``ccc grep`` and
``ccc search --text``.

Both print the same shape: a bold path header, then ``<line>| <text>`` rows behind
a dim gutter. Those styling primitives live here so the two stay in sync. What each
search draws *inside* a line differs — grep dims the context around a structural
match, text search highlights the matched spans — so that part stays with each
caller.
"""

from __future__ import annotations

import click


def paint(text: str, color: bool, **style: object) -> str:
    """``click.style(text, **style)`` when ``color`` is on, else ``text`` unchanged."""
    if not color or not text:
        return text
    return click.style(text, **style)  # type: ignore[arg-type]


def path_header(path: str, *, color: bool) -> str:
    """The bold path line that opens one file's matches."""
    return paint(path, color, fg="magenta", bold=True)


def gutter_width(max_line: int) -> int:
    """Right-align width for line numbers, so a file's gutters line up."""
    return len(str(max_line))


def gutter(line_no: int, width: int, *, color: bool) -> str:
    """The dim ``<line>| `` prefix — number, pipe, then exactly one space."""
    return paint(f"{line_no:>{width}}| ", color, fg="bright_black")
