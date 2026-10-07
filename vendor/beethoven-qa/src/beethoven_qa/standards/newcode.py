"""Which lines are new: the change between the base commit and the tree being checked, as git says.

The coverage and duplication groups measure the new lines only (SonarSource's conditions "on new
code"), and every group moves the base's findings of a renamed file to the file's new name before
matching them (``findings.moved``). Both facts come from git, read here from its own output:

- ``git diff --unified=0 <base>``: each hunk header ``@@ -a,b +c,d @@`` says lines ``c`` to
  ``c + d - 1`` of the file at the head are added or changed (``d`` is 1 when left out, and 0 for a
  hunk that only removes). A line is kept as a range, never as a set of numbers, so a hunk header
  that claims a billion lines costs nothing to hold.
- ``git diff --name-status -M -z <base>``: an ``R<score>`` entry names a file's old path and its new
  one.

A tree checked with no base (``--base none``) has every line new and no renamed file: ``Changes``
then says ``everything``.
"""

# Annotations are evaluated here (no ``from __future__ import annotations``): typeguard, in the
# test enclave, reads a NamedTuple's field types from the module of the function it checks, where a
# string such as "Changes" names nothing.

import re
from typing import NamedTuple

from beartype import beartype

from beethoven_qa.contracts import ensure, require

__all__ = ["Changes", "Ranges", "changed_lines", "new_lines", "renamed_files"]

# A hunk header of a unified diff; the old side is matched only to be skipped. Nine digits at most
# on the new side: no file has a billion lines, and int() never meets a run of digits it refuses.
_HUNK = re.compile(r"@@ -[0-9]+(?:,[0-9]+)? \+([0-9]{1,9})(?:,([0-9]{1,9}))? @@")

Ranges = tuple[tuple[int, int], ...]


class Changes(NamedTuple):
    """The new lines of a change: for each file, the inclusive line ranges the head adds or
    changes; the renamed files, as (path at the base, path at the head); and whether every line is
    new because there is no base to compare with."""

    lines: tuple[tuple[str, Ranges], ...]
    renamed: tuple[tuple[str, str], ...]
    everything: bool


def _ordered(result: tuple[tuple[str, Ranges], ...]) -> bool:
    paths = [p for p, _ in result]
    return paths == sorted(set(paths))


def _target(target: str) -> str | None:
    # The path of a ``+++`` header: ``b/<path>``, or git's quoted ``"b/<path>"`` with its escapes
    # undone; None for ``/dev/null`` and for anything else. Git ends the header with a tab when the
    # path holds a space (for GNU patch); a path that itself ends in a tab is always quoted.
    if len(target) >= 4 and target[0] == target[-1] == '"':
        target = _unquote(target[1:-1])
    elif target.endswith("\t"):
        target = target[:-1]
    return target[2:] if target.startswith("b/") and len(target) > 2 else None


# The escapes git writes in a quoted path (quote_c_style): one letter, or a byte in octal.
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_ESCAPE = re.compile(r"\\([0-7]{3}|.)", re.DOTALL)


def _unquote(text: str) -> str:
    def one(match: re.Match[str]) -> str:
        code = match.group(1)
        if len(code) == 3:
            return chr(int(code, 8) & 0xFF)
        return _ESCAPES.get(code, code)

    raw = _ESCAPE.sub(one, text)
    # An octal escape is one byte of the path's UTF-8; put the bytes back together.
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def _added(header: str) -> tuple[int, int] | None:
    # The lines a hunk header says the head adds, inclusive; None for a hunk that only removes.
    match = _HUNK.match(header)
    if match is None:
        return None
    start = int(match.group(1))
    count = 1 if match.group(2) is None else int(match.group(2))
    return (start, start + count - 1) if start >= 1 and count >= 1 else None


@beartype
@ensure(lambda result: _ordered(result), "each file once, in order of its path")
@ensure(
    lambda result: all(1 <= a <= b for _, ranges in result for a, b in ranges),
    "every range starts at line 1 or later and ends at or after its start",
)
@ensure(lambda result: all(len(r) > 0 for _, r in result), "a file is listed only with a new line")
@ensure(
    lambda result, diff: all(
        f"+{a},{b - a + 1} @@" in diff or (a == b and f"+{a} @@" in diff)
        for _, ranges in result
        for a, b in ranges
    ),
    "every range is the new side of a hunk header the diff holds",
)
def changed_lines(diff: str) -> tuple[tuple[str, Ranges], ...]:
    """The new lines of each file a ``git diff --unified=0`` output names, as inclusive ranges.

    A file's path is read from its ``+++ b/<path>`` header, which only the header part of a file's
    diff can hold: a content line that happens to start with ``+++`` is a line of a hunk, so the
    header is recognised only between ``diff --git`` and the file's first hunk. A deleted file
    (``+++ /dev/null``) has no new lines. A path git had to quote (one holding a tab, a newline, a
    quote or a backslash; ``core.quotePath=false`` leaves other characters alone) is unquoted.
    """
    found: dict[str, list[tuple[int, int]]] = {}
    path: str | None = None
    in_header = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            in_header, path = True, None
        elif in_header and line.startswith("+++ "):
            path = _target(line[4:])
        elif line.startswith("@@ "):
            in_header = False
            span = _added(line)
            if path is not None and span is not None:
                found.setdefault(path, []).append(span)
    return tuple((p, tuple(sorted(found[p]))) for p in sorted(found))


@beartype
@ensure(
    lambda result: len({old for old, _ in result}) == len(result), "a file is renamed at most once"
)
@ensure(lambda result: all(old != new for old, new in result), "a rename changes the path")
def renamed_files(name_status: str) -> tuple[tuple[str, str], ...]:
    """The renamed files a ``git diff --name-status -M -z`` output lists, as (old path, new path).

    With ``-z`` every field ends in a NUL and no path is quoted: a status, then one path, or two
    for a rename (``R<score>``) or a copy (``C<score>``). A copy keeps its source, so it is not a
    rename; an entry cut short by the end of the text is dropped.
    """
    fields = name_status.split("\x00")
    renames: dict[str, str] = {}
    i = 0
    while i < len(fields):
        status = fields[i]
        width = 3 if status[:1] in ("R", "C") else 2
        if status and i + width <= len(fields) and status[:1] == "R":
            old, new = fields[i + 1], fields[i + 2]
            if old and new and old != new and old not in renames:
                renames[old] = new
        i += width if status else 1
    return tuple(renames.items())


@beartype
@require(lambda changes: _ordered(changes.lines), "each file once, in order of its path")
@ensure(lambda result, lines: result <= lines, "only lines that were asked about")
@ensure(
    lambda result, changes, lines: not changes.everything or result == lines,
    "with no base, every line is new",
)
@ensure(
    lambda result, changes, path, lines: (
        changes.everything
        or all(
            any(a <= n <= b for p, ranges in changes.lines if p == path for a, b in ranges)
            for n in result
        )
    ),
    "a line is new only inside a range the diff gave its file",
)
@ensure(
    lambda result, changes, path, lines: (
        changes.everything
        or all(
            n in result
            for n in lines
            if any(a <= n <= b for p, ranges in changes.lines if p == path for a, b in ranges)
        )
    ),
    "every asked line inside such a range is new",
)
def new_lines(changes: Changes, path: str, lines: frozenset[int]) -> frozenset[int]:
    """Which of ``lines`` (of the file ``path`` at the head) are new in ``changes``."""
    if changes.everything:
        return lines
    ranges = next((r for p, r in changes.lines if p == path), ())
    return frozenset(n for n in lines if any(a <= n <= b for a, b in ranges))
