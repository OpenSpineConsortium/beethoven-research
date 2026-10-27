"""Readability: how hard a function is for a person, or a second agent, to follow.

The Drive file's section on computational and cognitive complexity, as four limits on every
function of the package:

- **Cognitive complexity at most 15** (G. Ann Campbell, SonarSource, 2018; 15 is SonarSource's
  default for Python, rule S3776), measured by complexipy. It adds one for each break in the
  linear flow and more for each level of nesting, which is the "arrow" shape generated code tends
  to take; the Drive file cites its correlation with the time people need to understand a function.
- **Cyclomatic complexity at most 10** (T. J. McCabe, 1976, whose own suggested limit it is),
  measured by ruff's C901: the paths a test suite has to reach.
- **At most 4 parameters**, ``self`` and ``cls`` not counted (Code Climate's default, flagging more
  than 4; ISO/IEC 5055's "over-parameterization"), measured by ruff's PLR0913. Several values that
  travel together are one NamedTuple.
- **At most 25 lines of code in a body** (Code Climate's default method length, which the Drive
  file quotes), measured by lizard as NLOC: the lines that hold code from the ``def`` to the
  function's last line, a docstring in triple quotes, comments and blank lines not counted, and a
  multi-line string counted where lizard counts it (one assigned or passed as an argument; one
  after ``yield`` or as a dict's value, and the pieces of an f-string, it takes for a comment and
  leaves out; measured 2026-10-08). NLOC counts the ``def`` line and each further line of the
  signature with the body, so the limit on it is 26, one for the ``def`` line, and a signature
  over several lines spends some of the 25. A function written on its ``def`` line
  (``def f(x): return x``) holds one line of code and is never counted, whether lizard lists it
  (it does when a deeper-indented line follows in the same block, and gives it that line's
  statement) or not.

Nested functions are measured on their own; of a nested function only its ``def`` line counts in
the function around it. lizard names a method without its class and a nested function
``outer.inner``, so each of its rows is matched to the module's syntax tree by file and start line
(the ``def`` line, for a decorated function and a multi-line signature alike) and anchored by the
qualified name found there, the anchor the group used before lizard, so old findings still match.
Two shapes lizard cannot be trusted on are refused with their reason instead of counted, at the
head of a change; at the base they are left out, so a change that removes one is never refused
for it: a function it leaves out although its body begins on a line of its own (a
``#lizard forgive`` or a ``GENERATED CODE`` comment hides one, and nothing in a repository's
files may loosen a limit; a nested function written on its ``def`` line, ``def inner(): ...``,
takes lizard's place of the function around it), and a function with a line at its ``def``'s
indentation after the parameters' closing parenthesis (a return annotation over several lines),
which lizard 1.24.1 takes for the function's end.

What is not here: the Buse-Weimer and Scalabrino readability models the Drive file describes are
classifiers trained on people's ratings, and no maintained open implementation of either exists to
run (looked for 2026-10-04); none is faked. Boolean-expression and nesting-depth limits exist in
ruff only as preview rules, which change without notice, so cognitive complexity, which already
charges for mixed boolean operators and for nesting, stands in for both.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
import tempfile
import tokenize
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa.contracts import ensure
from beethoven_qa.standards.findings import Finding, Measure, blocking, over_limit
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _bin,
    _compare,
    _definitions,
    _python_files,
    _read,
    _tool,
    _version,
)
from beethoven_qa.standards.sarif import Rule, Run

__all__ = ["GROUP", "LIMITS", "RULES", "body_length"]

GROUP = Group(
    "readability",
    "cognitive and cyclomatic complexity, parameters, function length",
    "Campbell's cognitive complexity, McCabe's cyclomatic complexity, Code Climate's limits",
)

COGNITIVE = "readability/cognitive-complexity"
CYCLOMATIC = "readability/cyclomatic-complexity"
PARAMETERS = "readability/parameters"
LENGTH = "readability/function-length"

# The most each rule allows. A repository cannot raise one (group.py says why); a change here is a
# release of beethoven-qa. LENGTH is in lizard's NLOC, which counts the def line with the body: the
# Drive file's 25 lines in the body plus one for the def line is 26, and each further line of a
# multi-line signature, counted too, comes out of the 25.
LIMITS: dict[str, int] = {COGNITIVE: 15, CYCLOMATIC: 10, PARAMETERS: 4, LENGTH: 26}
_WHAT = {
    COGNITIVE: "cognitive complexity",
    CYCLOMATIC: "cyclomatic complexity",
    PARAMETERS: "parameter count",
    LENGTH: "length in lines of code (the def line counted)",
}

RULES: dict[str, Rule] = {
    COGNITIVE: Rule(
        COGNITIVE,
        "CognitiveComplexity",
        "A function's cognitive complexity is at most 15.",
        "Cognitive Complexity (G. A. Campbell, SonarSource, 2018); 15 is SonarSource's default "
        "for Python (S3776).",
        "https://www.sonarsource.com/docs/CognitiveComplexity.pdf",
        ("maintainability", "readability"),
    ),
    CYCLOMATIC: Rule(
        CYCLOMATIC,
        "CyclomaticComplexity",
        "A function's cyclomatic complexity is at most 10.",
        "Cyclomatic complexity V(G) = E - N + 2P (T. J. McCabe, 1976); 10 is McCabe's own limit.",
        "https://docs.astral.sh/ruff/rules/complex-structure/",
        ("maintainability", "testability"),
    ),
    PARAMETERS: Rule(
        PARAMETERS,
        "ParameterCount",
        "A function takes at most 4 parameters, self and cls not counted.",
        "Over-parameterization (ISO/IEC 5055 maintainability); 4 is Code Climate's default.",
        "https://docs.astral.sh/ruff/rules/too-many-arguments/",
        ("maintainability", "readability"),
    ),
    LENGTH: Rule(
        LENGTH,
        "FunctionLength",
        "A function holds at most 26 lines of code as lizard counts them (NLOC): the def line and "
        "25 more, its signature and body together; docstring, comments and blanks not counted.",
        "Method length; 25 lines in the body is Code Climate's default, as the Drive file quotes "
        "it, and lizard's NLOC counts the def line with them.",
        "https://docs.codeclimate.com/docs/maintainability",
        ("maintainability", "readability"),
    ),
}


@beartype
@ensure(lambda result: result >= 0, "a count")
@ensure(
    lambda result, code, first, last: result == sum(1 for n in code if first <= n <= last),
    "the lines of code from the first to the last line, both included, and no others",
)
def body_length(code: frozenset[int], first: int, last: int) -> int:
    """How many of the lines numbered in ``code`` lie from ``first`` to ``last``: the count of a
    body the group made itself before lizard, kept as the oracle lizard's NLOC is held to on a
    plain body of N statements (``tests/test_standards_groups.py``), where the two must agree."""
    return sum(1 for n in code if first <= n <= last)


class _RuffItem(BaseModel):
    # One diagnostic of `ruff check --output-format json`: the fields read, the rest ignored.
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    filename: str
    location: dict[str, int]


class _CxItem(BaseModel):
    # One function of complexipy's JSON report.
    model_config = ConfigDict(extra="ignore")

    complexity: int
    function_name: str
    path: str


_OVER = re.compile(r"\(([0-9]+) > [0-9]+\)")
_RUFF_RULE = {"C901": CYCLOMATIC, "PLR0913": PARAMETERS}
# What `_layout` reads from a module's tokens: the first token of code on each line (column, text)
# and the line of the ) that closes each def's parameters, by the def's line.
_Layout = tuple[dict[int, tuple[int, str]], dict[int, int]]


def _relative(tree: Path, path: str) -> str:
    p = Path(path)
    return (p.resolve().relative_to(tree.resolve()) if p.is_absolute() else p).as_posix()


def _defs(
    tree: Path, rel: str, cache: dict[str, dict[int, tuple[str, int]]]
) -> dict[int, tuple[str, int]]:
    # A module's definitions, parsed once per run: ``setdefault`` would parse the module again for
    # every finding in it, since its default is built before it looks (measured 2026-10-08: 518
    # parses for one run over this package, nine tenths of the readability group's time).
    if rel not in cache:
        cache[rel] = _definitions(_read(tree, rel))
    return cache[rel]


def _span(tree: Path, rel: str, line: int, cache: dict[str, dict[int, tuple[str, int]]]) -> Measure:
    defs = _defs(tree, rel, cache)
    name, end = defs.get(line, (f"line {line}", line))
    return Measure("", rel, name, line, max(end, line), 0)


def _ruff(tree: Path, files: list[str]) -> list[Measure]:
    argv = [_bin("ruff"), "check", "--isolated", "--ignore-noqa", "--no-cache", "--exit-zero"]
    argv += ["--select", ",".join(_RUFF_RULE), "--target-version", "py311"]
    argv += ["--config", f"lint.mccabe.max-complexity={LIMITS[CYCLOMATIC]}"]
    argv += ["--config", f"lint.pylint.max-args={LIMITS[PARAMETERS]}", "--output-format", "json"]
    out = _tool([*argv, *(str(tree / f) for f in files)])
    if out.returncode != 0:
        raise RuntimeError(f"ruff failed: {out.stderr.strip()}")
    cache: dict[str, dict[int, tuple[str, int]]] = {}
    found = []
    for raw in json.loads(out.stdout or "[]"):
        item = _RuffItem.model_validate(raw)
        value = _OVER.search(item.message)
        if item.code in _RUFF_RULE and value is not None:
            rel = _relative(tree, item.filename)
            at = _span(tree, rel, item.location["row"], cache)
            found.append(at._replace(rule=_RUFF_RULE[item.code], value=int(value.group(1))))
    return found


def _complexipy(tree: Path, files: list[str], package: str) -> list[Measure]:
    if not files:
        return []
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-") as tmp:
        report = Path(tmp) / "complexipy.json"
        argv = [_bin("complexipy"), str(tree / package), "--output-format", "json", "--quiet"]
        argv += ["--output", str(report), "--no-ignore", "--cache-dir", str(Path(tmp) / "cache")]
        out = _tool(argv, cwd=Path(tmp))
        if not report.exists():
            raise RuntimeError(f"complexipy failed: {(out.stderr or out.stdout).strip()}")
        items = [_CxItem.model_validate(raw) for raw in json.loads(report.read_text("utf-8"))]
    cache: dict[str, dict[int, tuple[str, int]]] = {}
    found = []
    for item in items:
        rel = _relative(tree, item.path)
        defs = _defs(tree, rel, cache)
        name = item.function_name.replace("::", ".")
        line = next((n for n, (q, _) in sorted(defs.items()) if q == name), 1)
        end = defs.get(line, (name, line))[1]
        found.append(Measure(COGNITIVE, rel, name, line, max(end, line), item.complexity))
    return found


class _LizardRow(BaseModel):
    # One function of `lizard --csv`: NLOC, CCN, token, PARAM, length, location, file, name,
    # long_name, start, end; the three fields read. Not the name: lizard gives a method without
    # its class and a nested function as outer.inner, so the row is matched to the syntax tree by
    # its file and start line (the def line; measured 2026-10-08 for a decorated function and a
    # multi-line signature alike) and anchored by the qualified name found there.
    nloc: int
    file: str
    start: int


def _rows(tree: Path, stdout: str) -> dict[str, dict[int, int]]:
    # Each file's functions by start line, with their NLOC.
    found: dict[str, dict[int, int]] = {}
    for row in csv.reader(io.StringIO(stdout)):
        if len(row) < 11:
            raise RuntimeError(f"lizard printed a row of {len(row)} fields, not the 11 of --csv")
        item = _LizardRow.model_validate({"nloc": row[0], "file": row[6], "start": row[9]})
        found.setdefault(_relative(tree, item.file), {})[item.start] = item.nloc
    return found


def _layout(source: str) -> _Layout:
    # What lizard reads from the tokens: the first token of code on each line (its column is the
    # line's indentation to lizard; a comment or a blank line has none, and the inner lines of a
    # multi-line string are no line start) and, for each def, the line of the ) that closes its
    # parameters, after which lizard starts reading indentation. A module that does not tokenize
    # has neither; it does not parse either, and the group's other tools report the syntax error.
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    skip |= {tokenize.ENCODING, tokenize.ENDMARKER}
    starts: dict[int, tuple[int, str]] = {}
    closes: dict[int, int] = {}
    pending, depth = None, 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in skip:
                continue
            starts.setdefault(tok.start[0], (tok.start[1], tok.string))
            if tok.type == tokenize.NAME and tok.string == "def":
                pending, depth = tok.start[0], 0
            elif pending is not None and tok.string in ("(", ")"):
                depth += 1 if tok.string == "(" else -1
                if depth == 0:
                    closes[pending], pending = tok.start[0], None
    except (tokenize.TokenError, SyntaxError):
        return {}, {}
    return starts, closes


def _cut_short(node: ast.FunctionDef | ast.AsyncFunctionDef, layout: _Layout) -> int | None:
    # The first line after the parameters' ) that lizard would take for the function's end: one
    # at the def's indentation or less whose first token is not a ) (lizard's one exception). A
    # return annotation over several lines ends like that (measured 2026-10-08: such a function
    # was listed with the lines up to there and the rest of its body went to the module); a
    # continuation line someone dedented, which ruff format never leaves, is refused the same way
    # rather than measured.
    starts, closes = layout
    deeper = (node.col_offset + 1, "")  # a line with no code on it is no line start to lizard
    for n in range(closes.get(node.lineno, node.lineno) + 1, (node.end_lineno or node.lineno) + 1):
        column, text = starts.get(n, deeper)
        if column <= node.col_offset and not text.startswith(")"):
            return n
    return None


# Why the group refuses a function instead of counting it. The two comments the first sentence
# names are lizard's own: its forgiveness comment drops the function it closes, and its
# generated-code marker drops the rest of the file (measured 2026-10-08; neither phrase may appear
# in a comment of this module, or lizard applies it here).
_UNLISTED = (
    "`{name}` is not listed by lizard, as a `#lizard forgive` or a `GENERATED CODE` comment makes "
    "it (nothing in a repository's files may loosen a limit), or a nested function written on its "
    "def line (put its body on a line of its own)"
)
_CUT_SHORT = (
    "`{name}` has a line at its def's indentation (line {line}), which lizard takes for its end, "
    "as a return annotation over several lines does"
)


def _untrusted(
    source: str, defs: dict[int, tuple[str, int]], listed: dict[int, int]
) -> list[tuple[int, str]]:
    # The functions whose NLOC lizard does not give or gives wrong, each by its def line with its
    # reason, for the group to refuse rather than count: one lizard leaves out although its body
    # begins on a line of its own (one written on its def line holds one line of code and is
    # never counted, listed or not), and one lizard would cut short.
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    layout = _layout(source)
    problems = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name, first = defs[node.lineno][0], node.body[0]
        own_line = layout[0].get(first.lineno, (-1, ""))[0] == first.col_offset
        if own_line and node.lineno not in listed:
            problems.append((node.lineno, _UNLISTED.format(name=name)))
        if (cut := _cut_short(node, layout)) is not None:
            problems.append((node.lineno, _CUT_SHORT.format(name=name, line=cut)))
    return problems


def _measured(rel: str, source: str, listed: dict[int, int], strict: bool) -> list[Measure]:
    # One file's measures. ``strict``: refuse the file on a function lizard cannot be trusted on
    # (the head of a change); else leave such functions out (the base: a change that removes the
    # shape must not be refused for what the base held, and a finding on the fixed function then
    # counts as new).
    defs = _definitions(source)
    problems = _untrusted(source, defs, listed)
    if problems and strict:
        why = "; ".join(reason for _, reason in problems)
        raise RuntimeError(f"the length check cannot trust {rel}: {why}")
    doubtful = {line for line, _ in problems}
    found = []
    for start, nloc in sorted(listed.items()):
        name, end = defs.get(start, ("", start))
        # A function on its def line is one line of code whatever lizard charged it with.
        if start in defs and start not in doubtful and end > start:
            found.append(Measure(LENGTH, rel, name, start, end, nloc))
    return found


def _lizard(tree: Path, files: list[str], strict: bool = True) -> list[Measure]:
    if not files:
        return []
    argv = [_bin("lizard"), "--csv", "-l", "python", "--no-gitignore"]
    out = _tool([*argv, *(str(tree / f) for f in files)])
    if out.returncode != 0:
        raise RuntimeError(f"lizard failed: {out.stderr.strip()}")
    rows = _rows(tree, out.stdout)
    return [m for rel in files for m in _measured(rel, _read(tree, rel), rows.get(rel, {}), strict)]


def _findings(measures: list[Measure]) -> tuple[Finding, ...]:
    return tuple(
        f
        for m in measures
        if (f := over_limit(m, LIMITS[m.rule], _WHAT[m.rule], RULES[m.rule].tags)) is not None
    )


def _check(context: Context) -> Report:
    def ruff(tree: Path) -> tuple[Finding, ...]:
        return _findings(_ruff(tree, _python_files(tree, context.package)))

    def cognitive(tree: Path) -> tuple[Finding, ...]:
        files = _python_files(tree, context.package)
        return _findings(_complexipy(tree, files, context.package))

    def length(tree: Path) -> tuple[Finding, ...]:
        files = _python_files(tree, context.package)
        return _findings(_lizard(tree, files, strict=tree == context.root))

    tools = (
        (("complexipy", _version([_bin("complexipy"), "--version"])), cognitive, (COGNITIVE,)),
        (("ruff", _version([_bin("ruff"), "--version"])), ruff, (CYCLOMATIC, PARAMETERS)),
        (("lizard", _version([_bin("lizard"), "--version"])), length, (LENGTH,)),
    )
    runs = []
    for (name, version), analyze, rules in tools:
        head, states = _compare(context, analyze)
        uri = RULES[rules[0]].help_uri
        runs.append(
            Run(GROUP.name, name, version, uri, tuple(RULES[r] for r in rules), head, states)
        )
    stops = tuple(f for run in runs for f in blocking(run.findings, run.states))
    total = sum(len(run.findings) for run in runs)
    summary = f"{total} findings over a limit in the tree, {len(stops)} of them new or worse"
    return Report(GROUP.name, tuple(runs), stops, not stops, summary)
