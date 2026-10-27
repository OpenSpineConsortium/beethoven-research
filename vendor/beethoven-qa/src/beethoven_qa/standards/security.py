"""Security: weaknesses in the code that the Common Weakness Enumeration names, the catalogue
ISO/IEC 5055 builds its security and reliability measures on.

bandit (PyCQA) reads the package and gives every finding a severity, a confidence and the CWE it
belongs to; SonarSource's "Sonar way" allows no new vulnerability. A finding of **medium or high
severity found with medium or high confidence** is an error and blocks when the change made it; a
low one (importing ``subprocess``, CWE-78, is the usual one) is a note in the record, the way
SonarSource keeps a "security hotspot" for review apart from a vulnerability.

Each finding carries its CWE as the tag ``external/cwe/cwe-<n>``, the form GitHub's code scanning
reads. Whether a given CWE is one of the weaknesses ISO/IEC 5055 lists is not checked here: the
standard's own list is not reproduced in this repository.

Measured 2026-10-04 with bandit 1.9.4: left to itself, bandit honours a ``.bandit`` file in the
directory it reads (a ``skips`` line there hid a high-severity ``shell=True`` and an ``eval``) and a
``# nosec`` comment; so it runs with ``--ini`` naming an empty ``[bandit]`` section and with
``--ignore-nosec``, and both came back.

Measured 2026-10-08 with CrossHair 0.0.111: ``level_for``'s domain stated as a precondition over
two strings, each one of bandit's four grades, was one CrossHair could not meet (every condition
"Unable to meet precondition", so nothing behind them was checked, and the plain gate passed it in
silence); the same domain stated as ``Literal["UNDEFINED", ...]`` made CrossHair raise inside its
own argument builder (``get_type_hints`` on the Literal); as the enum ``Grade`` it is enumerated,
and both postconditions are confirmed over all paths. The model validates bandit's strings into
the enum at the boundary, and the callers pass the member.
"""

from __future__ import annotations

import enum
import json
import re
import tempfile
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa.contracts import ensure
from beethoven_qa.standards.findings import Finding, blocking
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

__all__ = ["BLOCKING", "GROUP", "Grade", "level_for"]

GROUP = Group("security", "weaknesses named by CWE", "CWE-mapped static analysis (ISO/IEC 5055)")


class Grade(enum.StrEnum):
    """bandit's four grades of a finding's severity and of its confidence, as its report spells
    them. An enum rather than a string checked against the four: CrossHair enumerates the members,
    where a precondition over strings was one it could not meet (the module docstring)."""

    UNDEFINED = "UNDEFINED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


BLOCKING = (Grade.MEDIUM, Grade.HIGH)  # the severities and confidences that make a finding an error
_HELP = "https://bandit.readthedocs.io/en/latest/plugins/index.html"


@beartype
@ensure(lambda result: result in ("error", "note"), "an error or a note")
@ensure(
    lambda result, severity, confidence: (
        (result == "error") == ({severity, confidence} <= {Grade.MEDIUM, Grade.HIGH})
    ),
    "an error exactly when both the severity and the confidence are medium or high",
)
def level_for(severity: Grade, confidence: Grade) -> str:
    """The SARIF level of a bandit finding of this severity and confidence."""
    return "error" if severity in BLOCKING and confidence in BLOCKING else "note"


class _Issue(BaseModel):
    # One result of bandit's JSON report: the fields read; the two grades validated into the enum.
    model_config = ConfigDict(extra="ignore")

    filename: str
    line_number: int
    line_range: list[int]
    test_id: str
    test_name: str
    issue_severity: Grade
    issue_confidence: Grade
    issue_text: str
    issue_cwe: dict[str, object] = {}
    more_info: str = _HELP


def _run_bandit(tree: Path, package: str) -> list[_Issue]:
    if not (tree / package).is_dir():
        return []
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-") as tmp:
        ini, report = Path(tmp) / "empty.ini", Path(tmp) / "bandit.json"
        ini.write_text("[bandit]\n", encoding="utf-8")
        argv = [_bin("bandit"), "-r", str(tree / package), "-f", "json", "-q", "--ignore-nosec"]
        out = _tool([*argv, "--ini", str(ini), "-o", str(report)], cwd=Path(tmp))
        if not report.exists():
            raise RuntimeError(f"bandit failed: {(out.stderr or out.stdout).strip()}")
        data = json.loads(report.read_text("utf-8"))
    if data.get("errors"):
        raise RuntimeError(f"bandit could not read: {data['errors']}")
    return [_Issue.model_validate(raw) for raw in data.get("results", [])]


def _anchor(source: str, defs: dict[int, tuple[str, int]], line: int) -> str:
    # The function around the line and the line's own text, whitespace collapsed: a finding that
    # only moved keeps its anchor. ``defs`` are the module's definitions, parsed once per module.
    around = [name for start, (name, end) in sorted(defs.items()) if start <= line <= end]
    lines = source.splitlines()
    text = " ".join(lines[line - 1].split()) if 1 <= line <= len(lines) else ""
    return f"{around[-1] if around else '<module>'}: {text}"


def _findings(tree: Path, package: str, rules: dict[str, Rule]) -> tuple[Finding, ...]:
    # The findings, and in ``rules`` the rule each of them names.
    found = []
    sources = {rel: _read(tree, rel) for rel in _python_files(tree, package)}
    defs: dict[str, dict[int, tuple[str, int]]] = {}
    for issue in _run_bandit(tree, package):
        rel = Path(issue.filename).resolve().relative_to(tree.resolve()).as_posix()
        cwe = issue.issue_cwe.get("id")
        tags = (f"external/cwe/cwe-{cwe}",) if isinstance(cwe, int) else ()
        rule = f"security/{issue.test_id}"
        rules[rule] = _rule(issue, tags)
        end = max(issue.line_range or [issue.line_number])
        level = level_for(issue.issue_severity, issue.issue_confidence)
        message = f"{issue.issue_text} ({issue.issue_severity.lower()} severity, CWE-{cwe})"
        if rel not in defs:
            defs[rel] = _definitions(sources.get(rel, ""))
        anchor = _anchor(sources.get(rel, ""), defs[rel], issue.line_number)
        found.append(Finding(rule, rel, issue.line_number, end, anchor, 0, 0, level, message, tags))
    return tuple(found)


def _rule(issue: _Issue, tags: tuple[str, ...]) -> Rule:
    name = "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", issue.test_name) if w)
    return Rule(
        f"security/{issue.test_id}",
        name or issue.test_id,
        f"bandit {issue.test_id} ({issue.test_name}).",
        "A weakness of the Common Weakness Enumeration (MITRE), the catalogue ISO/IEC 5055 maps "
        "its security and reliability measures to; Sonar way allows no new vulnerability.",
        issue.more_info,
        ("security", *tags),
    )


def _check(context: Context) -> Report:
    seen: dict[str, Rule] = {}
    head, states = _compare(context, lambda tree: _findings(tree, context.package, seen))
    rules = tuple(seen[r] for r in sorted({f.rule for f in head}))
    version = _version([_bin("bandit"), "--version"])
    run = Run(GROUP.name, "bandit", version, "https://bandit.readthedocs.io/", rules, head, states)
    stops = blocking(head, states)
    notes = sum(1 for f in head if f.level == "note")
    summary = (
        f"{len(head) - notes} weaknesses of medium or high severity in the tree, {len(stops)} new "
        f"or worse; {notes} low ones recorded"
    )
    return Report(GROUP.name, (run,), stops, not stops, summary)
