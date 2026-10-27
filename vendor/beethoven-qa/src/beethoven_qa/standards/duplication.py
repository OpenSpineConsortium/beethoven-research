"""Duplication: copied code, which the Drive file calls the mark of copy-paste programming and of
generated code that repeats itself instead of naming a function.

The condition is SonarSource's "Sonar way" quality gate: **at most 3% of the new lines are
duplicated**. The blocks come from jscpd (the copy/paste detector of the lock, 5.4.0), run on the
package's Python files in its "weak" mode, which compares the tokens of the code with the comments
set aside. The import statements and the docstrings are set aside too, as the detector this module
replaced set them aside (imports are alike everywhere, and a docstring is prose): the scan reads a
staged copy of each file with those lines blank, line for line, so the report's lines are the
file's. Identifiers are compared as written, so a block with its names changed is not a copy here,
and every module of the package is compared with every other and with itself.

A block is **at least 10 lines holding at least 40 of jscpd's tokens**. Ten lines is SonarSource's
own block for most languages. Its 100 tokens is not taken over, because it assumes about ten tokens
a line and Python does not have them: measured on this package on 2026-10-08, jscpd counts 7.4
tokens a line (40946 over 5569 lines, blank and comment lines among them), so a 10-line copy of
average code holds about 74, and the 13-line function the token-window detector this module
replaced let through (its floor was 100) holds 57, 4.4 a line, 48 in its first ten lines. The floor
of 40 is set so that a 10-line copy of ordinary code is a clone (those ten lines are one; jscpd's
own default of 50 would miss them) and a column of assignments or literals is not (ten
``name = value`` lines hold 30, three each; the tests' twelve-line function around ten of them
holds 35). Both conditions must hold, so nine dense lines in a row are not a clone, however many
tokens they carry. jscpd counts a clone's lines from its first token's line to its last token's,
so the lines between them count whatever they hold: a blank line, and a line a docstring or an
import held before the staging blanked it (measured 2026-10-08: two functions of eight code lines
with four blank lines after their ``def`` line are a 12-line clone of 78 tokens at this floor).
What the blanking takes away is the tokens, so a docstring never makes a block a clone, while the
lines it stood on still count toward the ten. Without the imports set aside, this package's two
pairs of identical import blocks (11 and 15 lines, 40 and 42 tokens) would be its only clones at
this floor.

Only the new lines count, the way SonarSource gates "new code": the density is the duplicated new
lines over the new lines of the package (lines that hold code; a blank line, a comment, an import
or a docstring is not one), and with no new line there is nothing to gate. Every clone at the head
is in the record, matched with the base commit's by the file it is a copy of and the text copied
(``findings``), so a clone the base already held is ``unchanged`` and never blocks; one that holds
new lines is ``updated``, since git reports a moved or edited block's lines as new. When the
density is over the limit, the copies that hold new lines are errors and block; the other copy of
a block newly copied, whose lines the base already held, is in the record as new and blocks
nothing, because it is not the change's to fix.

jscpd honours its own suppression comments, ``jscpd:ignore-start`` to ``jscpd:ignore-end``, and
has no flag to stop that. Measured 2026-10-08: it reads the marker wherever the text stands in a
file (a comment, a trailing comment, a string literal, a docstring, with or without a space after
the ``#``), and from the start marker to the end marker or the end of the file nothing is compared.
So the staged copy has the text defused (``jscpd-ignore``, which jscpd does not read, of the same
length), and a comment holding it is an error finding of its own rule: a suppression comment is
refused like every other from this release. A string literal holding the text, which a tool that
names the marker may need, is defused and not refused.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tempfile
import tokenize
from pathlib import Path
from typing import NamedTuple

from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import Finding, blocking, classify, moved
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _bin,
    _python_files,
    _read,
    _tool,
    _version,
)
from beethoven_qa.standards.newcode import Changes, new_lines
from beethoven_qa.standards.sarif import Rule, Run

__all__ = [
    "GROUP",
    "IGNORE",
    "LIMIT_PERCENT",
    "MARKER",
    "MIN_LINES",
    "MIN_TOKENS",
    "RULE",
    "in_blocks",
    "within_limit",
]

GROUP = Group("duplication", "copied code in the new lines", "SonarSource's Sonar way gate")

MIN_LINES = 10  # lines in the smallest duplicated block, SonarSource's
MIN_TOKENS = 40  # of jscpd's tokens in it, at least (derived above)
LIMIT_PERCENT = 3  # of the new lines, at most
MARKER = "jscpd:ignore"  # the text of jscpd's own suppression comments
_DEFUSED = "jscpd-ignore"  # what the staged copy holds instead: the same length, and not read
_HELP = "https://github.com/kucherenko/jscpd"

RULE = Rule(
    "duplication/new-lines",
    "DuplicatedNewLines",
    "At most 3% of the new lines lie in a block of 10 lines and 40 tokens that occurs elsewhere.",
    "Sonar way quality gate (SonarSource): duplicated lines on new code at most 3%; a block is 10 "
    "lines, SonarSource's own definition for most languages, holding 40 of jscpd's tokens, the "
    "floor derived for Python in this module.",
    "https://docs.sonarsource.com/sonarqube-server/latest/user-guide/code-metrics/metrics-definition/",
    ("maintainability", "duplication"),
)
IGNORE = Rule(
    "duplication/ignore-marker",
    "IgnoreMarker",
    "No comment holds jscpd:ignore: nothing a file says takes its code out of the scan.",
    "A suppression comment is refused like every other (ruff's noqa, bandit's nosec): the scan "
    "reads a copy with the marker defused, and the comment itself is an error.",
    _HELP,
    ("maintainability", "duplication"),
)

Block = tuple[int, int]  # (first line, last line) of a duplicated block, both included

# Tokens that make no line a line of code: a comment, the line structure, the bookkeeping.
_NOT_CODE = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


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


@beartype
@require(lambda blocks: all(1 <= a <= b for a, b in blocks), "each block a span of lines, from 1")
@ensure(lambda result, lines: result <= lines, "only lines that were asked about")
@ensure(
    lambda result, blocks: all(any(a <= n <= b for a, b in blocks) for n in result),
    "every line of the result lies in a block",
)
@ensure(
    lambda result, lines, blocks: all(
        n in result for n in lines if any(a <= n <= b for a, b in blocks)
    ),
    "every asked line inside a block is in the result",
)
def in_blocks(lines: frozenset[int], blocks: tuple[Block, ...]) -> frozenset[int]:
    """Which of ``lines`` lie inside one of ``blocks``: a file's duplicated new lines, when
    ``lines`` are its new lines and ``blocks`` the clones found in it."""
    return frozenset(n for n in lines if any(a <= n <= b for a, b in blocks))


class _Loc(BaseModel):
    # A position in jscpd's JSON report: the line from 1, the column from 0.
    model_config = ConfigDict(extra="ignore")

    line: int
    column: int


class _Side(BaseModel):
    # One side of a clone: its file, its first and last line, and where on them the copy starts
    # and ends. The report's ``fragment`` is not read: it holds the first side's whole lines, the
    # text before the copy on its first line among them, so it is not what the two sides share.
    model_config = ConfigDict(extra="ignore")

    name: str
    start: int
    end: int
    startLoc: _Loc
    endLoc: _Loc


class _Clone(BaseModel):
    # One entry of ``duplicates``: the two copies.
    model_config = ConfigDict(extra="ignore")

    firstFile: _Side
    secondFile: _Side


class _Module(NamedTuple):
    # One module of the package as the group reads it.
    rel: str
    source: str
    aside: frozenset[int]  # import and docstring lines: compared with nothing, counted as nothing


def _docstring(node: ast.AST) -> ast.Expr | None:
    body = getattr(node, "body", None)
    first = body[0] if isinstance(body, list) and body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first
    return None


def _aside(source: str) -> frozenset[int]:
    # The lines of the import statements and of the docstrings of the module, its classes and its
    # functions. A module that does not parse sets nothing aside; jscpd reads it all the same.
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    lines: set[int] = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        elif isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = _docstring(node)
            if doc is not None:
                lines.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    return frozenset(lines)


def _modules(tree: Path, package: str) -> list[_Module]:
    found = []
    for rel in _python_files(tree, package):
        source = _read(tree, rel)
        found.append(_Module(rel, source, _aside(source)))
    return found


def _staged(module: _Module) -> str:
    # The module as jscpd reads it: the set-aside lines blank and the marker defused, line for
    # line. The lines are split as the tokenizer splits them, so both number them alike.
    lines = io.StringIO(module.source).readlines()
    kept = ("\n" if n in module.aside else line for n, line in enumerate(lines, 1))
    return "".join(kept).replace(MARKER, _DEFUSED)


def _run_jscpd(staged: Path, package: str, out: Path) -> list[_Clone]:
    # From an empty working directory, as every tool here: measured 2026-10-08, jscpd reads a
    # .jscpd.json from the directory it is run in and from nowhere else, not from the scanned
    # directory nor its parents, so a repository's own file is never read.
    out.mkdir()
    argv = [_bin("jscpd"), str(staged / package), "--format", "python", "--mode", "weak"]
    argv += ["--min-lines", str(MIN_LINES), "--min-tokens", str(MIN_TOKENS), "--absolute"]
    argv += ["--reporters", "json", "--output", str(out), "--no-gitignore", "--silent", "--no-tips"]
    result = _tool(argv)
    report = out / "jscpd-report.json"
    if not report.exists():
        raise RuntimeError(f"jscpd failed: {(result.stderr or result.stdout).strip()}")
    data = json.loads(report.read_text("utf-8"))
    return [_Clone.model_validate(raw) for raw in data.get("duplicates", [])]


def _clones(package: str, staged: dict[str, str]) -> list[_Clone]:
    # The clones jscpd finds among the staged texts, written under a directory of their own, with
    # each side's path made relative to it (the report names the copy).
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-") as tmp:
        tree = Path(tmp) / "tree"
        for rel, text in staged.items():
            target = tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        clones = _run_jscpd(tree, package, Path(tmp) / "report")
        for clone in clones:
            for side in (clone.firstFile, clone.secondFile):
                side.name = Path(side.name).resolve().relative_to(tree.resolve()).as_posix()
    return clones


def _tokens(source: str) -> list[tokenize.TokenInfo]:
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return []


def _code_lines(tokens: list[tokenize.TokenInfo]) -> frozenset[int]:
    # The lines holding a token of code, each line of a string that spans several among them.
    return frozenset(
        n for t in tokens if t.type not in _NOT_CODE for n in range(t.start[0], t.end[0] + 1)
    )


def _new(module: _Module, changes: Changes | None) -> frozenset[int]:
    # The module's new lines of code, found only when the change reached it: tokenizing is the
    # slowest step here (measured 2026-10-08: half a second for this package's 20 modules, twice),
    # and a module the diff does not name has no new line to find. None is the base commit.
    if changes is None:
        return frozenset()
    if not changes.everything and all(rel != module.rel for rel, _ in changes.lines):
        return frozenset()
    code = _code_lines(_tokens(module.source)) - module.aside
    return new_lines(changes, module.rel, code)


def _marker(rel: str, comment: tokenize.TokenInfo) -> Finding:
    # Anchored by its line's text, like a weakness: moved, it is the same comment.
    text = f"a comment holds {MARKER}, jscpd's suppression comment: not honoured, and not allowed"
    where = " ".join(comment.line.split())
    line = comment.start[0]
    return Finding(IGNORE.id, rel, line, line, where, 0, 0, "error", text, IGNORE.tags)


def _markers(module: _Module) -> list[Finding]:
    # A comment holding the marker; a string holding it is not one. The text is looked for before
    # anything is tokenized, since most modules hold none.
    if MARKER not in module.source:
        return []
    comments = (t for t in _tokens(module.source) if t.type == tokenize.COMMENT)
    return [_marker(module.rel, t) for t in comments if MARKER in t.string]


def _blocks(clones: list[_Clone]) -> dict[str, tuple[Block, ...]]:
    found: dict[str, list[Block]] = {}
    for clone in clones:
        for side in (clone.firstFile, clone.secondFile):
            found.setdefault(side.name, []).append((side.start, side.end))
    return {rel: tuple(blocks) for rel, blocks in found.items()}


def _copied(side: _Side, staged: str) -> str:
    # The text of one side of a clone as the staged copy holds it, from the copy's first character
    # to its last: what the side is anchored by, so a block that only moved keeps its anchor.
    # jscpd's columns count the bytes of the line's UTF-8 (measured 2026-10-08: on a line holding
    # four accented letters and an emoji before the copy it said 19 where the copy's character
    # index was 11), so the two ends are cut as bytes; a cut never falls inside a character,
    # since a token boundary is one.
    lines = [line.encode("utf-8") for line in io.StringIO(staged).readlines()]
    lines = lines[side.start - 1 : side.end]
    if not lines:
        return ""
    lines[-1] = lines[-1][: side.endLoc.column]
    lines[0] = lines[0][side.startLoc.column :]
    return b"".join(lines).decode("utf-8", errors="replace")


def _copies(
    clone: _Clone, new: dict[str, frozenset[int]], passed: bool, staged: dict[str, str]
) -> list[Finding]:
    # One finding per side of a clone. Its anchor is the other file and the text copied; its value
    # is how many of its lines are new, what it adds to the density, which is 0 for every clone of
    # the base; it is an error only when the density is over the limit and it holds new lines.
    found = []
    sides = ((clone.firstFile, clone.secondFile), (clone.secondFile, clone.firstFile))
    for mine, other in sides:
        fresh = len(in_blocks(new.get(mine.name, frozenset()), ((mine.start, mine.end),)))
        level = "error" if fresh and not passed else "warning"
        text = f"lines {mine.start}-{mine.end} repeat a block also found at {other.name}:"
        text += f"{other.start}" + (f", {fresh} of them new" if fresh else "")
        copied = _copied(mine, staged.get(mine.name, "")).encode("utf-8", "surrogatepass")
        anchor = f"{other.name}: {hashlib.sha256(copied).hexdigest()[:16]}"
        span = (mine.start, mine.end)
        found.append(Finding(RULE.id, mine.name, *span, anchor, fresh, 0, level, text, RULE.tags))
    return found


class _Scan(NamedTuple):
    # One tree as scanned: its clones and marker comments as findings, and the density's counts.
    findings: tuple[Finding, ...]
    copied: int
    total: int


def _scan(tree: Path, package: str, changes: Changes | None) -> _Scan:
    # ``changes`` is None for the base commit, where no line is new.
    modules = _modules(tree, package)
    staged = {m.rel: _staged(m) for m in modules}
    clones = _clones(package, staged) if modules else []
    new = {m.rel: _new(m, changes) for m in modules}
    blocks = _blocks(clones)
    copied = sum(len(in_blocks(lines, blocks.get(rel, ()))) for rel, lines in new.items())
    total = sum(len(lines) for lines in new.values())
    passed = within_limit(copied, total)
    found = [f for clone in clones for f in _copies(clone, new, passed, staged)]
    found += [f for module in modules for f in _markers(module)]
    return _Scan(tuple(found), copied, total)


def _check(context: Context) -> Report:
    head = _scan(context.root, context.package, context.changes)
    old = _scan(context.base, context.package, None).findings if context.base is not None else ()
    states = classify(head.findings, moved(old, context.changes.renamed))
    stops = blocking(head.findings, states)
    version = _version([_bin("jscpd"), "--version"])
    run = Run(GROUP.name, "jscpd", version, _HELP, (RULE, IGNORE), head.findings, states)
    marks = sum(1 for f in head.findings if f.rule == IGNORE.id)
    summary = (
        f"{head.copied} of {head.total} new lines lie in a duplicated block "
        f"(at most {LIMIT_PERCENT}%)"
    )
    summary += f"; {marks} comments hold {MARKER}" if marks else ""
    passed = within_limit(head.copied, head.total) and not stops
    return Report(GROUP.name, (run,), stops, passed, summary)
