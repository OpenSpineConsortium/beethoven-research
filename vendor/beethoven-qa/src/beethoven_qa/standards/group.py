"""What a standards group is given and what it gives back, and the helpers the groups share.

A group is one module of this package with a ``GROUP`` (its name, the standard it implements and
what it checks) and a private ``_check(context) -> Report``. The runner (``standards.run``) hands
every group the same ``Context``: the checkout, the base commit's tree beside it, the package
directory and the new lines. A group reads, runs its tools and decides on its own, and its
``Report`` says whether it passed; no group reads another's result, so each can pass or fail, and
be changed, alone.

The tools run from an empty working directory with every setting on their command line, and their
suppression comments are not honoured (ruff's ``--ignore-noqa``, bandit's ``--ignore-nosec``,
complexipy's ``--no-ignore``): nothing in a repository's own files (a ``[tool.complexipy]`` table,
a ``.bandit`` file, a ``# noqa``) can loosen a limit the pinned copy sets. A repository changes a
limit only by taking another release of beethoven-qa.
"""

# Annotations are evaluated here (no ``from __future__ import annotations``): typeguard, in the
# test enclave, reads a NamedTuple's field types from the module of the function it checks, where a
# string such as "Changes" names nothing.

import ast
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from beethoven_qa.standards.findings import Finding, classify, moved
from beethoven_qa.standards.newcode import Changes
from beethoven_qa.standards.sarif import Run

__all__ = ["Context", "Group", "Report"]


class Group(NamedTuple):
    """A standards group as the runner lists it."""

    name: str  # the command-line name, e.g. "supply-chain"
    title: str  # what it checks, in a few words
    standard: str  # the standard of the Drive file it implements


class Context(NamedTuple):
    """What every group is given."""

    root: Path  # the checkout being checked
    base: Path | None  # the base commit's tree, extracted beside it; None when there is no base
    package: str  # the package directory, relative to the root, e.g. "src/beethoven_qa"
    changes: Changes


class Report(NamedTuple):
    """What a group gives back: the SARIF runs, what blocks, and its verdict in one sentence."""

    group: str
    runs: tuple[Run, ...]
    blocking: tuple[Finding, ...]
    passed: bool
    summary: str


def _python_files(tree: Path, package: str) -> list[str]:
    # The package's modules, as paths relative to the tree with forward slashes, in a stable order.
    # Caches and anything outside the package are never read.
    home = tree / package
    if not home.is_dir():
        return []
    return sorted(
        p.relative_to(tree).as_posix()
        for p in home.rglob("*.py")
        if "__pycache__" not in p.parts and p.is_file() and not p.is_symlink()
    )


def _read(tree: Path, rel: str) -> str:
    return (tree / rel).read_text(encoding="utf-8", errors="replace")


def _tool(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # A tool of the lock, run as its own process from an empty directory (no configuration file of
    # the repository's is found there) unless the caller names one; its output is read, never shown.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if cwd is not None:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-") as empty:
        return subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, check=False)


def _version(argv: list[str]) -> str:
    # "ruff 0.16.10" and "bandit 1.9.4\n  python version = ..." end their first line with the
    # version, "Coverage.py, version 7.16.2 with C extension" holds it in the middle (the log said
    # "extension" until 5.0.0, measured 2026-10-08): the first dotted number of the first line is
    # the version; a tool that cannot say is reported as such, never guessed.
    out = _tool(argv)
    lines = (out.stdout or out.stderr).strip().splitlines()[:1]
    return _version_word(lines[0]) if lines else "unknown"


def _version_word(line: str) -> str:
    words = [w.rstrip(",") for w in line.split()]
    dotted = [w for w in words if re.fullmatch(r"[0-9]+(\.[0-9]+)+", w) is not None]
    return dotted[0] if dotted else "unknown"


def _definitions(source: str) -> dict[int, tuple[str, int]]:
    # Every function and class of a module, by the line its ``def`` or ``class`` keyword is on:
    # its qualified name (outer.inner, Class.method) and its last line. A module that does not
    # parse has none; the group's own tool reports the syntax error.
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict[int, tuple[str, int]] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                found[child.lineno] = (name, child.end_lineno or child.lineno)
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _compare(
    context: Context, analyze: Callable[[Path], tuple[Finding, ...]]
) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    # One tool's findings at the head, and the state of each against the same tool's findings at
    # the base (moved to the head's file names). With no base, every finding is new.
    head = analyze(context.root)
    base = analyze(context.base) if context.base is not None else ()
    return head, classify(head, moved(base, context.changes.renamed))


def _bin(name: str) -> str:
    # A command of this environment: the one beside the interpreter, so a tool of the lock runs
    # and never another of the same name earlier on the PATH.
    beside = Path(sys.executable).parent / name
    return str(beside) if beside.exists() else name


def _spans(numbers: set[int] | frozenset[int]) -> list[tuple[int, int]]:
    # Line numbers as runs of consecutive lines, (first, last) each, in order.
    spans: list[tuple[int, int]] = []
    for n in sorted(numbers):
        if spans and n == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], n)
        else:
            spans.append((n, n))
    return spans
