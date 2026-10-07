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
  file quotes), counted here: lines that hold code, from the first statement after the docstring
  to the function's last line, the docstring, comments and blank lines not counted.

Nested functions are measured on their own and count towards the function around them.

What is not here: the Buse-Weimer and Scalabrino readability models the Drive file describes are
classifiers trained on people's ratings, and no maintained open implementation of either exists to
run (looked for 2026-10-04); none is faked. Boolean-expression and nesting-depth limits exist in
ruff only as preview rules, which change without notice, so cognitive complexity, which already
charges for mixed boolean operators and for nesting, stands in for both.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tempfile
import tokenize
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa import __version__
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
# release of beethoven-qa.
LIMITS: dict[str, int] = {COGNITIVE: 15, CYCLOMATIC: 10, PARAMETERS: 4, LENGTH: 25}
_WHAT = {
    COGNITIVE: "cognitive complexity",
    CYCLOMATIC: "cyclomatic complexity",
    PARAMETERS: "parameter count",
    LENGTH: "body length in lines of code",
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
        "A function body holds at most 25 lines of code, docstring, comments and blanks not "
        "counted.",
        "Method length; 25 lines is Code Climate's default, as the Drive file quotes it.",
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
    """How many of the lines numbered in ``code`` lie from ``first`` to ``last``."""
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


def _relative(tree: Path, path: str) -> str:
    p = Path(path)
    return (p.resolve().relative_to(tree.resolve()) if p.is_absolute() else p).as_posix()


def _span(tree: Path, rel: str, line: int, cache: dict[str, dict[int, tuple[str, int]]]) -> Measure:
    defs = cache.setdefault(rel, _definitions(_read(tree, rel)))
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
        defs = cache.setdefault(rel, _definitions(_read(tree, rel)))
        name = item.function_name.replace("::", ".")
        line = next((n for n, (q, _) in sorted(defs.items()) if q == name), 1)
        end = defs.get(line, (name, line))[1]
        found.append(Measure(COGNITIVE, rel, name, line, max(end, line), item.complexity))
    return found


def _code_lines(source: str) -> frozenset[int]:
    # The lines that hold a token of code: comments, blank lines and the indentation bookkeeping
    # do not count; a token spanning lines (a long string) counts on each of them.
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    skip |= {tokenize.ENCODING, tokenize.ENDMARKER}
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type not in skip:
                lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        return frozenset()
    return frozenset(lines)


def _first_statement(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = node.body
    docstring = (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    rest = body[1:] if docstring else body
    return rest[0].lineno if rest else (node.end_lineno or node.lineno) + 1


def _lengths(tree: Path, files: list[str]) -> list[Measure]:
    found = []
    for rel in files:
        source = _read(tree, rel)
        try:
            module = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        code, defs = _code_lines(source), _definitions(source)
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                last = node.end_lineno or node.lineno
                name = defs.get(node.lineno, (node.name, last))[0]
                value = body_length(code, _first_statement(node), last)
                found.append(Measure(LENGTH, rel, name, node.lineno, last, value))
    return found


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
        return _findings(_lengths(tree, _python_files(tree, context.package)))

    tools = (
        (("complexipy", _version([_bin("complexipy"), "--version"])), cognitive, (COGNITIVE,)),
        (("ruff", _version([_bin("ruff"), "--version"])), ruff, (CYCLOMATIC, PARAMETERS)),
        (("beethoven-qa", __version__), length, (LENGTH,)),
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
