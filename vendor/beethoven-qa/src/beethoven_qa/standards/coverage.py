"""Coverage: whether the tests reach the new lines.

The condition is SonarSource's "Sonar way" quality gate: **at least 80% of the new lines that can
run are run by the tests**. The repository's own test suite runs under coverage.py (line coverage,
the package only), and the share is taken over the new lines alone; a change with no new line
that can run has nothing to gate.

coverage.py is given a configuration file of its own, so a repository's ``[tool.coverage]`` table
cannot leave a file out, and that file excludes nothing, so a ``# pragma: no cover`` does not take a
new line out of the count. The tests run as the repository runs them (``pytest`` from its root, with
its own pytest settings); if they fail under coverage, the group fails and says so, since a share
of a failing run means nothing.

Code a test reaches only through another process (a command line started with ``subprocess``) is
not counted as run: coverage.py's ``patch = subprocess`` would count it, and it would also trace
every other Python process the tests start, CrossHair's among them, which traces code itself.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import Finding
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _python_files,
    _spans,
    _tool,
    _version,
)
from beethoven_qa.standards.newcode import new_lines
from beethoven_qa.standards.sarif import Rule, Run

__all__ = ["GROUP", "MINIMUM_PERCENT", "RULE", "meets_minimum", "new_line_coverage"]

GROUP = Group("coverage", "tests reaching the new lines", "SonarSource's Sonar way gate")

MINIMUM_PERCENT = 80

RULE = Rule(
    "coverage/new-lines",
    "CoverageOfNewLines",
    "The tests run at least 80% of the new lines that can run.",
    "Sonar way quality gate (SonarSource): coverage on new code at least 80%; line coverage, "
    "measured by coverage.py.",
    "https://coverage.readthedocs.io/",
    ("testability",),
)

# A pattern no line holds, so nothing is excluded from the count (coverage.py's own default would
# exclude every line carrying "pragma: no cover").
_CONFIG = """[run]
branch = false

[report]
exclude_lines =
    ^this line is never excluded by beethoven-qa$
"""


@beartype
@require(lambda executed, missing: not executed & missing, "a line ran or did not, never both")
@ensure(lambda result: 0 <= result[0] <= result[1], "covered lines are among those that can run")
@ensure(
    lambda result, executed, missing, new: (
        result == (len(executed & new), len((executed | missing) & new))
    ),
    "the new lines that ran, over the new lines that can run",
)
def new_line_coverage(
    executed: frozenset[int], missing: frozenset[int], new: frozenset[int]
) -> tuple[int, int]:
    """How many of the ``new`` lines of one file ran, and how many of them can run at all."""
    return len(executed & new), len((executed | missing) & new)


@beartype
@require(lambda covered, coverable: 0 <= covered <= coverable, "covered lines can run")
@ensure(
    lambda result, covered, coverable: (
        result == (coverable == 0 or 100 * covered >= MINIMUM_PERCENT * coverable)
    ),
    "met exactly when the covered share is at least the minimum, or nothing can run",
)
def meets_minimum(covered: int, coverable: int) -> bool:
    """Whether ``covered`` of ``coverable`` new lines is at least ``MINIMUM_PERCENT`` percent."""
    return coverable == 0 or 100 * covered >= MINIMUM_PERCENT * coverable


class _File(BaseModel):
    model_config = ConfigDict(extra="ignore")

    executed_lines: list[int]
    missing_lines: list[int]


class _CoverageJson(BaseModel):
    model_config = ConfigDict(extra="ignore")

    files: dict[str, _File]


def _measure(root: Path, package: str) -> tuple[dict[str, _File], str]:
    # The tests under coverage.py; the per-file lines, and why nothing was measured if so.
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-") as tmp:
        rc, data, report = Path(tmp) / "coveragerc", Path(tmp) / "data", Path(tmp) / "c.json"
        rc.write_text(_CONFIG, encoding="utf-8")
        common = [f"--rcfile={rc}", f"--data-file={data}"]
        run = [sys.executable, "-m", "coverage", "run", *common, f"--source={root / package}"]
        out = _tool([*run, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root)
        if out.returncode != 0:
            tail = "\n".join((out.stdout + out.stderr).strip().splitlines()[-15:])
            return {}, f"the tests failed under coverage:\n{tail}"
        _tool([sys.executable, "-m", "coverage", "json", *common, "-o", str(report)], cwd=root)
        if not report.exists():
            return {}, "coverage.py wrote no report (nothing of the package ran in the tests)"
        parsed = _CoverageJson.model_validate_json(report.read_text("utf-8"))
    files = {}
    for name, lines in parsed.files.items():
        path = Path(name) if Path(name).is_absolute() else root / name
        files[path.resolve().relative_to(root.resolve()).as_posix()] = lines
    return files, ""


def _uncovered(rel: str, lines: frozenset[int], failed: bool) -> list[Finding]:
    level = "error" if failed else "note"
    return [
        Finding(
            RULE.id,
            rel,
            a,
            b,
            f"{rel}:{a}",
            b - a + 1,
            0,
            level,
            f"new lines {a}-{b} never ran in the tests",
            RULE.tags,
        )
        for a, b in _spans(lines)
    ]


def _tally(
    context: Context, measured: dict[str, _File]
) -> tuple[int, int, dict[str, frozenset[int]]]:
    # The new lines that ran and that can run, over the package, and per file the new lines missed.
    covered = coverable = 0
    missed: dict[str, frozenset[int]] = {}
    for rel in _python_files(context.root, context.package):
        lines = measured.get(rel)
        executed = frozenset(lines.executed_lines) if lines else frozenset()
        missing = frozenset(lines.missing_lines) if lines else frozenset()
        new = new_lines(context.changes, rel, executed | missing)
        ran, can = new_line_coverage(executed, missing, new)
        covered, coverable = covered + ran, coverable + can
        missed[rel] = missing & new
    return covered, coverable, missed


def _check(context: Context) -> Report:
    measured, problem = _measure(context.root, context.package)
    version = _version([sys.executable, "-m", "coverage", "--version"])
    if problem:
        run = Run(GROUP.name, "coverage.py", version, RULE.help_uri, (RULE,), (), ())
        return Report(GROUP.name, (run,), (), False, problem)
    covered, coverable, missed = _tally(context, measured)
    passed = meets_minimum(covered, coverable)
    head = tuple(f for rel, lines in missed.items() for f in _uncovered(rel, lines, not passed))
    states = ("new",) * len(head)
    run = Run(GROUP.name, "coverage.py", version, RULE.help_uri, (RULE,), head, states)
    summary = f"the tests ran {covered} of {coverable} new lines that can run"
    summary += f" (at least {MINIMUM_PERCENT}%)"
    stops = tuple(f for f in head if f.level == "error")
    return Report(GROUP.name, (run,), stops, passed, summary)
