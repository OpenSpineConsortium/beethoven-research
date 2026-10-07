"""Duplication: copied code, which the Drive file calls the mark of copy-paste programming and of
generated code that repeats itself instead of naming a function.

The condition is SonarSource's "Sonar way" quality gate: **at most 3% of the new lines are
duplicated**, a duplicated block being, as SonarSource's detector defines it for most languages, at
least 10 lines holding at least 100 tokens that occur elsewhere too. Here a block is compared token
by token after the comments, the docstrings and the import statements are set aside (imports are
alike everywhere, and a docstring is prose); identifiers are compared as written, so a block with
its names changed is not a copy here. Every module of the package is compared with every other and
with itself.

Only the new lines count, the way SonarSource gates "new code": the density is the duplicated new
lines over the new lines of the package, and with no new line there is nothing to gate.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from typing import NamedTuple

from beartype import beartype

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import Finding
from beethoven_qa.standards.group import Context, Group, Report, _python_files, _read, _spans
from beethoven_qa.standards.newcode import new_lines
from beethoven_qa.standards.sarif import Rule, Run

__all__ = ["GROUP", "LIMIT_PERCENT", "MIN_TOKENS", "RULE", "WINDOW", "repeated", "within_limit"]

GROUP = Group("duplication", "copied code in the new lines", "SonarSource's Sonar way gate")

WINDOW = 10  # lines in the smallest duplicated block
MIN_TOKENS = 100  # tokens in it, at least
LIMIT_PERCENT = 3  # of the new lines, at most

RULE = Rule(
    "duplication/new-lines",
    "DuplicatedNewLines",
    "At most 3% of the new lines lie in a block of 10 lines and 100 tokens that occurs elsewhere.",
    "Sonar way quality gate (SonarSource): duplicated lines on new code at most 3%; a block is 10 "
    "lines and 100 tokens, SonarSource's own definition for most languages.",
    "https://docs.sonarsource.com/sonarqube-server/latest/user-guide/code-metrics/metrics-definition/",
    ("maintainability", "duplication"),
)

Line = tuple[int, str, int]  # (line number, what the line holds, tokens on it)
Place = tuple[int, int]  # (index of the file, index in its lines of a block's first line)


def _key(files: tuple[tuple[Line, ...], ...], place: Place, window: int) -> tuple[str, ...]:
    f, i = place
    return tuple(k for _, k, _ in files[f][i : i + window])


def _tokens(files: tuple[tuple[Line, ...], ...], place: Place, window: int) -> int:
    f, i = place
    return sum(t for _, _, t in files[f][i : i + window])


def _increasing(lines: tuple[Line, ...]) -> bool:
    numbers = [n for n, _, _ in lines]
    pairs = zip(numbers, numbers[1:], strict=False)
    return all(n >= 1 for n in numbers) and all(a < b for a, b in pairs)


@beartype
@require(lambda window: window >= 1, "a block holds at least one line")
@require(lambda min_tokens: min_tokens >= 0, "a count of tokens")
@require(lambda files: all(_increasing(f) for f in files), "each file's lines in order, from 1")
@ensure(lambda result: all(len(group) >= 2 for group in result), "a repeat occurs twice or more")
@ensure(
    lambda result, files, window: all(
        0 <= f < len(files) and 0 <= i <= len(files[f]) - window
        for group in result
        for f, i in group
    ),
    "every block lies within its file",
)
@ensure(
    lambda result, files, window: all(
        len({_key(files, p, window) for p in group}) == 1 for group in result
    ),
    "the blocks of a group hold the same lines",
)
@ensure(
    lambda result, files, window, min_tokens: all(
        _tokens(files, p, window) >= min_tokens for group in result for p in group
    ),
    "every block holds enough tokens",
)
@ensure(
    lambda result: (
        len({p for group in result for p in group}) == sum(len(group) for group in result)
    ),
    "no block is in two groups",
)
def repeated(
    files: tuple[tuple[Line, ...], ...], window: int, min_tokens: int
) -> tuple[tuple[Place, ...], ...]:
    """The blocks of ``window`` consecutive lines, holding at least ``min_tokens`` tokens, that
    occur more than once across ``files``, grouped by what they hold."""
    seen: dict[tuple[str, ...], list[Place]] = {}
    for f, lines in enumerate(files):
        for i in range(len(lines) - window + 1):
            if _tokens(files, (f, i), window) >= min_tokens:
                seen.setdefault(_key(files, (f, i), window), []).append((f, i))
    return tuple(tuple(places) for places in seen.values() if len(places) >= 2)


@beartype
@require(lambda new: new >= 0, "a count of lines")
@require(lambda duplicated, new: 0 <= duplicated <= new, "duplicated new lines are new lines")
@ensure(
    lambda result, duplicated, new: result == (100 * duplicated <= LIMIT_PERCENT * new),
    "within the limit exactly when the duplicated share is at most the limit",
)
@ensure(lambda result, duplicated: duplicated > 0 or result, "no duplicated line always passes")
def within_limit(duplicated: int, new: int) -> bool:
    """Whether ``duplicated`` of ``new`` new lines is at most ``LIMIT_PERCENT`` percent."""
    return 100 * duplicated <= LIMIT_PERCENT * new


def _set_aside(module: ast.Module) -> set[int]:
    # Lines of docstrings and import statements: compared with nothing.
    lines: set[int] = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _lines(source: str) -> tuple[Line, ...]:
    # Each line's tokens, as one text whose digest stands for the line: what a block is compared by.
    try:
        aside = _set_aside(ast.parse(source))
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, tokenize.TokenError):
        return ()
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    skip |= {tokenize.ENCODING, tokenize.ENDMARKER}
    held: dict[int, list[str]] = {}
    for tok in tokens:
        if tok.type not in skip and tok.start[0] not in aside:
            held.setdefault(tok.start[0], []).append(f"{tok.type}:{tok.string}")
    return tuple((n, _digest(held[n]), len(held[n])) for n in sorted(held))


def _digest(tokens: list[str]) -> str:
    return hashlib.sha256("\x00".join(tokens).encode("utf-8", "surrogatepass")).hexdigest()


def _marked(
    files: tuple[tuple[Line, ...], ...], groups: tuple[tuple[Place, ...], ...]
) -> list[dict[int, Place]]:
    # For every duplicated line, where another copy of a block holding it starts.
    marks: list[dict[int, Place]] = [{} for _ in files]
    for group in groups:
        for f, i in group:
            other = next(p for p in group if p != (f, i))
            for n, _, _ in files[f][i : i + WINDOW]:
                marks[f].setdefault(n, other)
    return marks


class _Scan(NamedTuple):
    # The package's files as compared, and for each where its duplicated lines are copied from.
    paths: list[str]
    files: tuple[tuple[Line, ...], ...]
    marks: list[dict[int, Place]]


def _blocks(scan: _Scan, index: int, new: frozenset[int], level: str) -> list[Finding]:
    # One finding per run of duplicated lines of one file that holds a new line.
    rel, mark, found = scan.paths[index], scan.marks[index], []
    for start, end in _spans(set(mark)):
        fresh = {n for n in new if start <= n <= end}
        if fresh:
            f, i = mark[start]
            where = f"{scan.paths[f]}:{scan.files[f][i][0]}"
            text = f"lines {start}-{end} repeat a block also found at {where}"
            found.append(
                Finding(RULE.id, rel, start, end, where, len(fresh), WINDOW, level, text, RULE.tags)
            )
    return found


def _check(context: Context) -> Report:
    paths = _python_files(context.root, context.package)
    files = tuple(_lines(_read(context.root, rel)) for rel in paths)
    scan = _Scan(paths, files, _marked(files, repeated(files, WINDOW, MIN_TOKENS)))
    new = [
        new_lines(context.changes, rel, frozenset(n for n, _, _ in lines))
        for rel, lines in zip(paths, files, strict=True)
    ]
    total = sum(len(lines) for lines in new)
    copied = sum(len(lines & mark.keys()) for lines, mark in zip(new, scan.marks, strict=True))
    passed = within_limit(copied, total)
    level = "warning" if passed else "error"
    head = tuple(f for i, lines in enumerate(new) for f in _blocks(scan, i, lines, level))
    states = ("new",) * len(head)
    run = Run(GROUP.name, "beethoven-qa", __version__, RULE.help_uri, (RULE,), head, states)
    summary = f"{copied} of {total} new lines lie in a duplicated block (at most {LIMIT_PERCENT}%)"
    return Report(GROUP.name, (run,), () if passed else head, passed, summary)
