"""The code standards as a session edits: ``python -m beethoven_qa coach``, run by the plugin's
Claude Code hook after every ``Edit`` or ``Write`` (``plugin/hooks/``).

The hook hands the command the call it follows, as JSON on stdin (the edited file's absolute path
is ``tool_input.file_path``). The command runs the groups that are cheap enough to run on every
edit over the checkout, against the same base CI uses (``auto``: where the branch left the default
branch), and answers with the findings that would block in CI in the file just edited, as the
hook's JSON (``decision: block`` and a ``reason``, which Claude Code puts beside the edit's result
for the session to read; the edit itself has already happened and stays). A file nothing would
block, a file outside the package and the workflows, and anything that cannot run (no
``[tool.beethoven-qa]`` table, no base, a tool missing) get no answer at all: coaching never stops
a session and never fails it, and CI still decides.

The coverage group is left out: it runs the tests, which is not something to do on every edit. The
limits are the pinned copy's own, the same that CI applies; nothing here loosens one.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from beartype import beartype
from pydantic import BaseModel, ConfigDict, ValidationError

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards import design, duplication, readability, security, supply_chain
from beethoven_qa.standards.findings import Finding
from beethoven_qa.standards.group import Context, Report
from beethoven_qa.standards.run import _line, _prepared
from beethoven_qa.vendor import is_safe_relpath

__all__ = ["CHEAP", "WORKFLOW", "groups_for", "reason"]

# The groups that coach an edit of a module of the package, and of a workflow file: everything but
# coverage, which runs the tests.
CHEAP = (
    readability.GROUP.name,
    design.GROUP.name,
    duplication.GROUP.name,
    security.GROUP.name,
)
WORKFLOW = (supply_chain.GROUP.name,)
_MODULES = {m.GROUP.name: m for m in (readability, design, duplication, security, supply_chain)}
# The most findings one answer lists; the rest are counted, so a session is not handed a wall.
_SHOWN = 12


@beartype
@require(lambda rel: is_safe_relpath(rel), "a path inside the checkout, relative to its root")
@require(lambda package: is_safe_relpath(package), "the package directory, relative to the root")
@ensure(
    lambda result: result in ((), CHEAP, WORKFLOW), "the cheap groups, the workflow one or none"
)
@ensure(
    lambda result, rel, package: (
        (result == CHEAP) == (rel.startswith(f"{package}/") and rel.endswith(".py"))
    ),
    "a module of the package gets every cheap group, and nothing else does",
)
@ensure(
    lambda result, rel: result != WORKFLOW or rel.startswith(f"{supply_chain.WORKFLOWS}/"),
    "only a file of the workflows directory gets the supply-chain group",
)
def groups_for(rel: str, package: str) -> tuple[str, ...]:
    """The groups that coach an edit of the file at ``rel`` in a checkout whose package is
    ``package``."""
    if rel.startswith(f"{package}/") and rel.endswith(".py"):
        return CHEAP
    folder, _, name = rel.rpartition("/")
    if folder == supply_chain.WORKFLOWS and name.endswith((".yml", ".yaml")):
        return WORKFLOW
    return ()


@beartype
@require(lambda shown: shown >= 1, "at least one finding is shown")
@ensure(
    lambda result, lines: (result == "") == (len(lines) == 0), "silent exactly when nothing blocks"
)
@ensure(
    lambda result, lines, shown: all(line in result for line in lines[:shown]),
    "every finding up to the limit is said",
)
@ensure(
    lambda result, lines, shown: len(lines) <= shown or f"and {len(lines) - shown} more" in result,
    "the findings left out are counted",
)
def reason(rel: str, lines: tuple[str, ...], shown: int) -> str:
    """What the session reads beside its edit of ``rel``: the first ``shown`` of the findings that
    would block in CI, one per line, how many more there are, and what to do."""
    if not lines:
        return ""
    head = f"beethoven-qa {__version__}'s code standards would block this change in CI, in {rel}:"
    rest = f"  and {len(lines) - shown} more\n" if len(lines) > shown else ""
    said = "".join(f"  {line}\n" for line in lines[:shown])
    fix = (
        "Fix each at its cause, as the code-standards skill says; a limit is never raised and a "
        "suppression comment is never read. CI decides; this is said now so it is fixed now."
    )
    return f"{head}\n{said}{rest}{fix}"


class _Call(BaseModel):
    # The hook's input: the fields read, the rest ignored.
    model_config = ConfigDict(extra="ignore")

    tool_input: dict[str, object] = {}


def _edited(root: Path, text: str) -> str | None:
    # The edited file, relative to the root with forward slashes; None when the input names none,
    # or names one outside the checkout or one that is not a plain file.
    try:
        call = _Call.model_validate_json(text)
    except ValidationError:
        return None
    raw = call.tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw) if Path(raw).is_absolute() else root / raw
    if path.is_symlink() or not path.is_file():
        return None
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    return rel if is_safe_relpath(rel) else None


def _said(report: Report, rel: str) -> list[str]:
    # The report's blocking findings in the edited file, each as the standards command prints it.
    states = {f: s for run in report.runs for f, s in zip(run.findings, run.states, strict=True)}
    mine: list[Finding] = [f for f in report.blocking if f.path == rel]
    return [_line(f, states.get(f, "new")).strip() for f in mine]


def _group(context: Context, name: str, rel: str) -> list[str]:
    # A group that cannot run says nothing; the others still do.
    try:
        report: Report = _MODULES[name]._check(context)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError):
        return []
    return _said(report, rel)


def _blocking(context: Context, names: tuple[str, ...], rel: str) -> tuple[str, ...]:
    # The groups at once, each in its own thread: most of their time is their tools' processes, so
    # the answer comes in about the time of the slowest group instead of the sum; the order of the
    # lines is the order of ``names`` whatever finishes first.
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        said = list(pool.map(lambda name: _group(context, name, rel), names))
    return tuple(line for lines in said for line in lines)


def _answer(root: Path, text: str) -> str:
    # The hook's JSON answer, or "" for no answer at all.
    rel = _edited(root, text)
    if rel is None:
        return ""
    with _prepared(root, "auto") as (context, _):
        names = groups_for(rel, context.package) if is_safe_relpath(context.package) else ()
        said = reason(rel, _blocking(context, names, rel), _SHOWN) if names else ""
    return json.dumps({"decision": "block", "reason": said}) if said else ""


def _run(root: Path, text: str) -> str:
    """What the command prints: the hook's answer, or nothing when there is nothing to say or
    anything stood in the way."""
    try:
        return _answer(root.resolve(), text)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return ""
