"""What every standards group reports, and how a finding in the new code is told from an old one.

Why the new code only: measured on 2026-10-04, this package's own command-line and git functions
(``gate._run``, ``vendor._check`` and others) break the readability limits, and so would most code
written before the standards existed; gated over the whole tree, every pull request in every
repository would be red on its first day. So a finding blocks a merge only when the change made it:
SonarSource's "clean as you code", with the old findings still reported, and the comparison done
the way SARIF 2.1.0 describes it (``baselineState``, matched through ``partialFingerprints``).

A finding is matched with the base commit's by its rule, its file and its anchor: what it is about,
independent of the line it sits on (a function's qualified name, a class's, or the text of the
line). Moving a function down a file therefore changes nothing; making it worse does. When both
sides hold findings with the same key, they are paired worst with worst: a head finding paired with
a base finding of a lower value is ``updated`` (the change made it worse) and blocks; one paired
with a base finding at least as bad is ``unchanged``; one left over is ``new``.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

from beartype import beartype

from beethoven_qa.contracts import ensure, require

__all__ = [
    "LEVELS",
    "STATES",
    "Finding",
    "Measure",
    "blocking",
    "classify",
    "fingerprint",
    "moved",
    "over_limit",
]

# SARIF's own levels: an error blocks when it is new or worse, a warning and a note are recorded.
LEVELS: tuple[str, ...] = ("error", "warning", "note")
# The SARIF baselineState values a finding at the head commit can take ("absent" belongs to a
# finding of the base that is gone, which the report counts but never blocks on).
STATES: tuple[str, ...] = ("new", "updated", "unchanged")


class Finding(NamedTuple):
    """One result of one rule, at one place, in a form every group shares and SARIF can carry."""

    rule: str  # the group's rule id, e.g. "readability/cognitive-complexity"
    path: str  # the file, relative to the repository root, with forward slashes
    line: int  # first line, from 1
    end_line: int  # last line, at least ``line``
    anchor: str  # what the finding is about, independent of its line: a qualified name or a text
    value: int  # what was measured (a complexity, a count); 0 when the rule measures nothing
    limit: int  # the most the rule allows; 0 when there is no limit
    level: str  # one of LEVELS
    message: str
    tags: tuple[str, ...] = ()  # e.g. "external/cwe/cwe-78", the form GitHub reads


def _key(finding: Finding) -> tuple[str, str, str]:
    return (finding.rule, finding.path, finding.anchor)


@beartype
@ensure(
    lambda result: len(result) == 64 and all(c in "0123456789abcdef" for c in result),
    "a sha256 in lower-case hex",
)
def fingerprint(rule: str, path: str, anchor: str) -> str:
    """The SARIF ``partialFingerprints`` value of a finding: its rule, file and anchor, never its
    line, so a finding that only moved keeps it.

    Each part is written with its length first, so two different triples never give the same text.
    """
    text = "".join(f"{len(part)}:{part}" for part in (rule, path, anchor))
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@beartype
@ensure(lambda result, head: len(result) == len(head), "one state for each finding")
@ensure(lambda result: all(s in STATES for s in result), "a SARIF baselineState")
@ensure(
    lambda result, head, base: all(
        s == "new" for f, s in zip(head, result, strict=True) if _key(f) not in set(map(_key, base))
    ),
    "a finding whose rule, file and anchor the base never had is new",
)
@ensure(
    lambda result, head, base: all(
        sum(1 for g, s in zip(head, result, strict=True) if _key(g) == _key(f) and s != "new")
        == min(
            sum(1 for g in head if _key(g) == _key(f)), sum(1 for b in base if _key(b) == _key(f))
        )
        for f in head
    ),
    "as many findings are matched with the base as both sides hold, and no more",
)
@ensure(
    lambda result, head, base: all(
        f.value > min(b.value for b in base if _key(b) == _key(f))
        for f, s in zip(head, result, strict=True)
        if s == "updated"
    ),
    "a finding is updated only when it is worse than a base finding it could be paired with",
)
@ensure(
    lambda result, head, base: all(
        f.value <= max(b.value for b in base if _key(b) == _key(f))
        for f, s in zip(head, result, strict=True)
        if s == "unchanged"
    ),
    "a finding is unchanged only when the base held one at least as bad",
)
@ensure(
    lambda result, head, base: head != base or all(s == "unchanged" for s in result),
    "a tree compared with itself has nothing new",
)
def classify(head: tuple[Finding, ...], base: tuple[Finding, ...]) -> tuple[str, ...]:
    """The SARIF ``baselineState`` of each finding at the head commit, against the base's findings.

    ``base`` holds the base commit's findings with their paths already moved to where the head has
    the files (``moved``), so a renamed file keeps its old findings.
    """
    states = ["new"] * len(head)
    for key in {_key(f) for f in head}:
        mine = [i for i, f in enumerate(head) if _key(f) == key]
        mine.sort(key=lambda i: -head[i].value)
        theirs = sorted((b.value for b in base if _key(b) == key), reverse=True)
        for i, old in zip(mine, theirs, strict=False):
            states[i] = "updated" if head[i].value > old else "unchanged"
    return tuple(states)


@beartype
@require(lambda head, states: len(head) == len(states), "one state for each finding")
@require(lambda states: all(s in STATES for s in states), "SARIF baselineStates")
@ensure(lambda result: all(f.level == "error" for f in result), "only an error blocks")
@ensure(
    lambda result, head, states: (
        list(result)
        == [f for f, s in zip(head, states, strict=True) if f.level == "error" and s != "unchanged"]
    ),
    "every error the change made or made worse blocks, in the order found, and nothing else",
)
def blocking(head: tuple[Finding, ...], states: tuple[str, ...]) -> tuple[Finding, ...]:
    """The findings that stop a merge: errors that are new or worse than the base's."""
    return tuple(
        f for f, s in zip(head, states, strict=True) if f.level == "error" and s != "unchanged"
    )


@beartype
@require(
    lambda renamed: len({old for old, _ in renamed}) == len(renamed),
    "a file of the base is renamed at most once",
)
@ensure(lambda result, base: len(result) == len(base), "every finding is kept")
@ensure(
    lambda result, base, renamed: all(
        r == f._replace(path=dict(renamed).get(f.path, f.path))
        for r, f in zip(result, base, strict=True)
    ),
    "only the path changes, and only for a renamed file, to its new name",
)
def moved(base: tuple[Finding, ...], renamed: tuple[tuple[str, str], ...]) -> tuple[Finding, ...]:
    """The base commit's findings with each renamed file's path replaced by its name at the head,
    so a file that was only moved keeps its findings matched."""
    where = dict(renamed)
    return tuple(f._replace(path=where.get(f.path, f.path)) for f in base)


class Measure(NamedTuple):
    """One function's or class's value under one rule, before it is compared with a limit."""

    rule: str
    path: str
    name: str  # its qualified name, e.g. "Class.method"; the anchor of a finding
    line: int  # the line of its def or class
    end_line: int
    value: int


@beartype
@require(lambda limit: limit >= 0, "a limit is a count, never negative")
@require(lambda measure: 1 <= measure.line <= measure.end_line, "a span of lines of the file")
@ensure(
    lambda result, measure, limit: (result is None) == (measure.value <= limit),
    "a finding exactly when the value is over the limit",
)
@ensure(
    lambda result, measure, limit: (
        result is None
        or (
            result.level == "error"
            and (result.rule, result.path, result.anchor)
            == (measure.rule, measure.path, measure.name)
            and (result.line, result.end_line) == (measure.line, measure.end_line)
            and (result.value, result.limit) == (measure.value, limit)
        )
    ),
    "the finding is an error about that function or class, carrying its value and the limit",
)
def over_limit(
    measure: Measure, limit: int, what: str, tags: tuple[str, ...] = ()
) -> Finding | None:
    """The finding for a value over its limit, or ``None``; ``what`` names the value in the
    message ("cognitive complexity")."""
    if measure.value <= limit:
        return None
    return Finding(
        measure.rule,
        measure.path,
        measure.line,
        measure.end_line,
        measure.name,
        measure.value,
        limit,
        "error",
        f"`{measure.name}`: its {what} is {measure.value}, more than {limit}",
        tags,
    )
