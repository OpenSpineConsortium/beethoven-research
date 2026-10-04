"""``.claudeignore``: a PreToolUse hook that keeps Claude Code's file tools off ignored paths.

The fourth part of greg's state-management guide (Drive, 2026-10-04). The guide is right that
Claude Code itself does not read a ``.claudeignore`` and that the native tools are permission deny
rules and the sandbox's ``denyRead``; it then offers a hook that greps the requested path in the
ignore file, which matches the wrong way round (it asks whether the path occurs in the file, so
``node_modules/`` never stops ``/repo/node_modules/x.js`` and a path that happens to occur as text
is refused). This is the same idea done properly: the file is read as gitignore patterns (``#``
comments, ``!`` re-includes, a trailing ``/`` for folders, anchored patterns, ``*``, ``?``,
``[...]`` and ``**``; the last matching line wins and an ignored folder hides everything in it),
and the file tools' paths are judged against it (``Read``, ``Edit``, ``Write``, ``MultiEdit``,
``NotebookEdit``, and the folder a ``Grep``, ``Glob`` or ``LS`` starts from).

A refusal is the JSON ``permissionDecision: deny`` with the reason, which Claude reads and works
around rather than retrying. ``bee init`` also turns the file into native ``Read`` deny rules, so
the harness's own coverage of shell file commands applies where the patterns allow it.

What it is not: a wall. A search started above an ignored folder still walks it, and a script can
read anything; secrets belong in the deny rules (the image's managed settings deny the sign-in
directory and credential files whatever this file says) and outside the project. So, unlike the
guard, this hook fails open: a broken pattern file must not stop every read, and it says so on
stderr (exit 1, which Claude Code shows as a non-blocking hook error).

The image runs this file by path, like guard.py, so it imports nothing of the package.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from beartype import beartype
from icontract import ensure, require

__all__ = [
    "IGNORE_FILE",
    "Rule",
    "compile_rules",
    "decide",
    "deny_rules",
    "glob_regex",
    "ignored_by",
    "main",
]

IGNORE_FILE = ".claudeignore"
# Never read through a file tool, whatever the project's file says: the sign-in's own file.
ALWAYS_DENIED_NAMES: tuple[str, ...] = (".credentials.json",)
_JUDGED = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob", "LS")
_GLOB_CHARS = frozenset("*?[")


@runtime_checkable
class _TextStream(Protocol):
    def read(self) -> str: ...

    def write(self, text: str, /) -> int: ...


@dataclass(frozen=True)
class Rule:
    """One pattern line: its text as written, whether it re-includes, whether it names folders
    only, and the regular expression it compiles to (matched against a ``/``-separated path
    relative to the ignore file's folder)."""

    source: str
    negate: bool
    dir_only: bool
    regex: str


@beartype
@ensure(lambda result: "\n" not in result, "one expression")
def glob_regex(pattern: str) -> str:
    """Translate one gitignore glob (already without ``!``, the leading and the trailing ``/``)
    into a regular-expression body: ``*`` and ``?`` stop at ``/``; ``**`` crosses folders only as
    a whole segment, as in git (``foo**bar`` is ``foo*bar``)."""
    # crosshair: per_condition_timeout=3
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            whole = pattern[i : i + 2] == "**" and (i == 0 or pattern[i - 1] == "/")
            if whole and pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if whole and i + 2 == len(pattern):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            while pattern[i + 1 : i + 2] == "*":  # a run of stars inside a segment is one star
                i += 1
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 2)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                negated = body[:1] in ("!", "^")
                body = re.escape(body[1:] if negated else body).replace("\\-", "-")
                out.append(("[^" if negated else "[") + body + "]")
                i = j + 1
                continue
        elif c == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out).replace("\n", "")


@beartype
@ensure(lambda result, text: len(result) <= text.count("\n") + 1, "at most one rule per line")
@ensure(
    lambda result: all(r.source and not r.source.startswith("#") for r in result),
    "blank lines and comments make no rule",
)
def compile_rules(text: str) -> tuple[Rule, ...]:
    """Read a ``.claudeignore`` into rules, in file order. A line whose pattern cannot compile is
    skipped rather than allowed to break every read."""
    # crosshair: per_condition_timeout=3
    rules: list[Rule] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.endswith("\\") and raw[len(line) : len(line) + 1] == " ":
            line += " "  # "\ " keeps one trailing space, as in git
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        body = line[1:] if negate else line
        if body.startswith(("\\#", "\\!")):
            body = body[1:]
        dir_only = body.endswith("/")
        body = body.rstrip("/")
        if not body:
            continue
        anchored = "/" in body
        body = body.lstrip("/")
        regex = ("^" if anchored else "^(?:.*/)?") + glob_regex(body) + "$"
        try:
            re.compile(regex)
        except re.error:
            continue
        rules.append(Rule(source=line, negate=negate, dir_only=dir_only, regex=regex))
    return tuple(rules)


def _last_match(rules: tuple[Rule, ...], rel: str, is_dir: bool) -> Rule | None:
    hit: Rule | None = None
    for rule in rules:
        if rule.dir_only and not is_dir:
            continue
        if re.match(rule.regex, rel):
            hit = rule
    return hit


@beartype
@require(lambda rel: not rel.startswith("/") and "\\" not in rel, "a relative /-separated path")
@ensure(
    lambda result, rules: result == "" or any(r.source == result and not r.negate for r in rules),
    "a refusal names an ignoring rule of the file",
)
def ignored_by(rules: tuple[Rule, ...], rel: str, is_dir: bool) -> str:
    """The pattern that ignores ``rel`` (relative to the ignore file's folder), or "". As in git, a
    path inside an ignored folder stays ignored whatever a later ``!`` line says about it."""
    # crosshair: off
    # Each rule carries a regular expression as data; a symbolic pattern is beyond CrossHair
    # (0.0.111 stops with an internal error), so the tests sample this one instead.
    parts = [p for p in rel.split("/") if p and p != "."]
    for depth in range(1, len(parts)):
        hit = _last_match(rules, "/".join(parts[:depth]), True)
        if hit is not None and not hit.negate:
            return hit.source
    if not parts:
        return ""
    hit = _last_match(rules, "/".join(parts), is_dir)
    return hit.source if hit is not None and not hit.negate else ""


@beartype
@ensure(
    lambda result, rules: result == () or not any(r.negate for r in rules),
    "a file that re-includes anything gets no deny rules (a deny rule cannot be undone)",
)
@ensure(lambda result: all(r.startswith("Read(./") and r.endswith(")") for r in result), "Read")
def deny_rules(rules: tuple[Rule, ...]) -> tuple[str, ...]:
    """Native ``Read`` deny rules for the same patterns, in the permission syntax (paths relative
    to the settings file's project, ``./``). Only when no line re-includes: a deny rule cannot be
    undone by a later one, so a file with ``!`` lines keeps the hook alone."""
    # crosshair: per_condition_timeout=3
    if any(r.negate for r in rules):
        return ()
    out: list[str] = []
    for rule in rules:
        pattern = rule.source.lstrip("\\").rstrip("/")
        anchored = "/" in pattern
        pattern = pattern.lstrip("/")
        if any(c in pattern for c in "()"):
            continue  # not expressible inside Read(...) without ambiguity
        base = pattern if anchored else "**/" + pattern
        out.append(f"Read(./{base}/**)" if rule.dir_only else f"Read(./{base})")
        if not rule.dir_only:
            out.append(f"Read(./{base}/**)")
    return tuple(dict.fromkeys(out))


def _literal_prefix(glob: str) -> str:
    """The folders of a Glob pattern before its first wildcard (``node_modules/**/*.js`` ->
    ``node_modules``; a pattern with no wildcard names a file, so its folder)."""
    parts = glob.replace("\\", "/").split("/")
    literal: list[str] = []
    for part in parts:
        if any(c in _GLOB_CHARS for c in part):
            break
        literal.append(part)
    if len(literal) == len(parts):
        literal = literal[:-1]
    return "/".join(p for p in literal if p)


def _relative(path: str, root: str) -> str | None:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return None  # another drive on Windows: outside the project
    rel = rel.replace("\\", "/")
    if rel == ".." or rel.startswith("../"):
        return None
    return "" if rel == "." else rel


@beartype
@ensure(lambda result: result is None or result != "", "None to allow, else the reason")
def decide(payload: dict[str, object], environ: dict[str, str] | None = None) -> str | None:
    """Judge one PreToolUse event: None lets it through, a string is the reason to refuse it."""
    # crosshair: off
    tool = payload.get("tool_name")
    if tool not in _JUDGED:
        return None
    env = dict(os.environ) if environ is None else environ
    cwd = str(payload.get("cwd") or env.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    project = os.path.abspath(env.get("CLAUDE_PROJECT_DIR") or cwd)
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    candidates: list[tuple[str, bool]] = []
    if raw:
        target = str(raw)
        target = target if os.path.isabs(target) else os.path.join(cwd, target)
        folder_tool = tool in ("Grep", "Glob", "LS")
        candidates.append((os.path.normpath(target), folder_tool and os.path.isdir(target)))
    elif tool in ("Grep", "Glob", "LS"):
        candidates.append((cwd, True))
    globbed = {"Glob": tool_input.get("pattern"), "Grep": tool_input.get("glob")}.get(str(tool))
    if isinstance(globbed, str):
        prefix = _literal_prefix(globbed)
        if prefix:
            base = candidates[0][0] if candidates else cwd
            candidates.append((os.path.normpath(os.path.join(base, prefix)), True))
    for path, is_dir in list(candidates):
        real = os.path.realpath(path)
        if real != path:
            candidates.append((real, is_dir))
    for path, _ in candidates:
        if os.path.basename(path) in ALWAYS_DENIED_NAMES:
            return f"{path} is a Claude Code sign-in file; nothing reads it"
    text = None
    try:
        with open(os.path.join(project, IGNORE_FILE), encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return None
    rules = compile_rules(text)
    for path, is_dir in candidates:
        rel = _relative(path, project)
        if rel is None or rel == "":
            continue
        pattern = ignored_by(rules, rel, is_dir)
        if pattern:
            return (
                f"{rel} matches the pattern '{pattern}' in {IGNORE_FILE}: it is outside this "
                "session's reads and edits. Work from other files, or ask the person to change "
                f"{IGNORE_FILE}."
            )
    return None


@beartype
@ensure(lambda result: result in (0, 1), "0 judged, 1 failed open")
def main(stdin: _TextStream, stdout: _TextStream, stderr: _TextStream) -> int:
    """Read one event, print a deny decision when an ignored path is touched, answer 0; on any
    failure let the call through and say so (exit 1, a non-blocking error)."""
    # crosshair: off
    try:
        payload = json.loads(stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 - fail open, loudly: the deny rules still hold secrets
        stderr.write(
            f"claudeignore: could not judge the call ({exc.__class__.__name__}); allowed\n"
        )
        return 1
    if reason:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            stdout,
        )
        stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin, sys.stdout, sys.stderr))
