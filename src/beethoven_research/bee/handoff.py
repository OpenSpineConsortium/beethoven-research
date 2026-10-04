"""The state hand-off: continuous project state, the hand-off file, and the hooks that keep both.

Parts two to five of greg's state-management guide (Drive, "Claude CLI State Management Guide",
2026-10-04), built so a session can stop on one account and the next session, on another account,
another machine or a compute node, picks the work up without the conversation:

- ``PROJECT_STATE.md``, generated from git after every edit, write or shell call (the guide's
  ``update_state.sh``) and injected when a session resumes, forks, compacts or starts a hand-off;
- ``claude_context.md``, the hand-off file in the guide's six-section schema, written by Claude
  (``/handoff``, or when the Stop gate asks) or pasted from claude.ai, validated and repaired
  coercively here, never trusted blindly by the session that reads it (the hydration steps);
- the status line, which is where Claude Code hands a subscriber's 5-hour and 7-day usage to a
  script (the statusline page, read 2026-10-04); it shows the account and its window, keeps the
  last reading for the switcher, and warns at the hand-off threshold and at the guide's 60 % of
  context;
- a ``StopFailure`` hook that records a usage limit the moment Claude Code reports it.

Everything lands in one state folder per project or run (``.claude/state``, or the run directory's
``state`` in a job), which ignores itself in git. Nothing here reads inside a sign-in directory:
the transcript path is recorded from the hook's own input, never looked up.

The image runs this file by path before the engine's package is bound (as it runs guard.py), so
it imports nothing of the package. Every hook answers exit 0: state upkeep must never stop a
session, and a failure is written to the state folder's ``hook-errors.log`` instead.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from beartype import beartype
from icontract import ensure, require

__all__ = [
    "CONTEXT_FILE",
    "HYDRATE_FILE",
    "INJECT_LIMIT",
    "LIMIT_FILE",
    "LOG_FILE",
    "SESSION_FILE",
    "PHASES",
    "SECTIONS",
    "STATE_FILE",
    "ContextReport",
    "Snapshot",
    "extraction_prompt",
    "fingerprint",
    "gate_feedback",
    "hydration_prompt",
    "context_report",
    "install_context",
    "main",
    "parse_context",
    "read_record",
    "redact",
    "refresh_state",
    "render_state",
    "state_dir",
    "write_record",
    "statusline_text",
    "truncate",
]

STATE_FILE = "PROJECT_STATE.md"
CONTEXT_FILE = "claude_context.md"
EXTRACT_FILE = "EXTRACT.md"
HYDRATE_FILE = "HYDRATE.md"
SESSION_FILE = "session.json"
CONTEXT_RECORD = "context.json"
GATE_RECORD = "gate.json"
LIMIT_FILE = "limit.json"
USAGE_FILE = "usage.json"
LOG_FILE = "handoff.log"
ERROR_LOG = "hook-errors.log"

# Claude Code caps what a hook injects at 10,000 characters (hooks reference, SessionStart); the
# state stays under it with room for the one-line notes that ride with it.
INJECT_LIMIT = 9500
# The guide's token-exhaustion failure: a hand-off file that is not small costs the next session
# its first turn. Above this the report warns; it never refuses.
CONTEXT_WARN_BYTES = 8192
DEFAULT_THRESHOLD = 90
# The guide's 60 % rule: compact with a focus before context rot, not at the warning.
COMPACT_HINT_PCT = 60

PHASES: tuple[str, ...] = ("Planning", "Implementation", "Debugging", "Refactoring")
SECTIONS: tuple[tuple[int, str], ...] = (
    (1, "Primary Objective"),
    (2, "Architectural Decisions & Constraints"),
    (3, "Files Modified & State"),
    (4, "Completed Milestones"),
    (5, "Active Blockers & Errors"),
    (6, "Immediate Next Actions (Hydration Directives)"),
)
_SECTION_WORDS: dict[int, tuple[str, ...]] = {
    1: ("objective",),
    2: ("architectural", "decision", "constraint"),
    3: ("files modified", "file state", "files"),
    4: ("milestone", "completed"),
    5: ("blocker", "error"),
    6: ("next action", "hydration", "next step"),
}
_FILE_STATUSES = ("Complete", "Pending", "Broken")

SCHEMA = """# CLAUDE STATE SERIALIZATION

**Timestamp:** [current UTC time] **Phase:** [Planning / Implementation / Debugging / Refactoring]

## 1. Primary Objective

[A precise, 2-3 sentence description of the overarching goal of this workflow.]

## 2. Architectural Decisions & Constraints

- [Non-negotiable stack choices, design patterns and boundaries established in this session.]
- [Specific libraries, versions and naming conventions.]

## 3. Files Modified & State

| File Path | Status | Key Modifications / Purpose |
| --- | --- | --- |
| [relative path] | [Complete/Pending/Broken] | [what changed and why] |

## 4. Completed Milestones

1. [Exactly what has been implemented and verified, and how it was verified.]

## 5. Active Blockers & Errors

- [EXACT error messages, stack traces or failing test output, copied, never summarised.]
- [The current working hypothesis for the root cause.]

## 6. Immediate Next Actions (Hydration Directives)

1. [3-5 imperative steps the next session runs at once, e.g. "Run grep to verify X in Y."]
"""


@runtime_checkable
class _TextStream(Protocol):
    def read(self) -> str: ...

    def write(self, text: str, /) -> int: ...


@dataclass(frozen=True)
class Snapshot:
    """What git says about the project, gathered at the edge and rendered by ``render_state``."""

    repo: bool
    root: str = ""
    branch: str = ""
    head: str = ""
    ahead: int = -1
    behind: int = -1
    status: tuple[str, ...] = ()
    diffstat: tuple[str, ...] = ()
    log: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextReport:
    """What ``parse_context`` made of a hand-off file: found, missing, repaired, warned."""

    ok: bool
    phase: str
    timestamp: str
    present: tuple[int, ...]
    missing: tuple[int, ...]
    files: tuple[tuple[str, str], ...]
    next_actions: int
    repairs: tuple[str, ...]
    warnings: tuple[str, ...]
    size: int


# --- pure logic ----------------------------------------------------------------------------------


@beartype
@require(lambda limit: limit >= 40, "room for the marker")
@ensure(lambda result, limit: len(result) <= limit, "never longer than the limit")
@ensure(lambda result, text, limit: len(text) > limit or result == text, "short text is untouched")
def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, saying how much was cut (no silent caps)."""
    # crosshair: per_condition_timeout=4
    if len(text) <= limit:
        return text
    marker = f"\n[... {len(text)} characters in all; cut here]\n"
    keep = max(0, limit - len(marker))
    return (text[:keep] + marker)[:limit]


def _clip(line: str, width: int = 160) -> str:
    return line if len(line) <= width else line[: width - 1] + "…"


def _flat(text: str, width: int) -> str:
    """One line of plain text: control characters become spaces (CrossHair found a reset time
    with a newline in it breaking the status line's one line)."""
    return _clip("".join(c if " " <= c != "\x7f" else " " for c in text), width)


def _bounded(lines: tuple[str, ...], most: int) -> list[str]:
    shown = [_clip(x) for x in lines[:most]]
    if len(lines) > most:
        shown.append(f"... and {len(lines) - most} more")
    return shown


@beartype
@ensure(lambda result: len(result) <= INJECT_LIMIT, "fits what a hook may inject")
@ensure(lambda result: result.startswith("# PROJECT STATE"), "a recognisable heading")
def render_state(snap: Snapshot, stamp: str, account: str, session: str, handoff: str) -> str:
    """``PROJECT_STATE.md`` from a git snapshot: deterministic, bounded, every cut announced."""
    # crosshair: per_condition_timeout=3
    out = [
        "# PROJECT STATE (written by Beethoven from git after every change; do not edit)",
        "",
        f"Updated: {_clip(stamp, 40)} · account: {_clip(account or 'unnamed', 40)}"
        f" · session: {_clip(session or 'unknown', 40)}",
    ]
    if not snap.repo:
        out += ["", "Not a git repository: only the hand-off file below carries the state."]
    else:
        up = (
            f" · upstream: {snap.ahead} ahead, {snap.behind} behind"
            if snap.ahead >= 0 and snap.behind >= 0
            else " · no upstream"
        )
        out.append(
            f"Repository: {_clip(snap.root, 120)} · branch: {_clip(snap.branch, 60)}"
            f" · HEAD: {_clip(snap.head[:12] or 'none yet', 12)}{up}"
        )
        out += ["", f"## Uncommitted changes ({len(snap.status)})", ""]
        out += _bounded(snap.status, 30) or ["(none: the working tree matches HEAD)"]
        if snap.diffstat:
            out += ["", "## Diff against HEAD", ""] + _bounded(snap.diffstat, 12)
        out += ["", "## Recent commits", ""] + (_bounded(snap.log, 8) or ["(none yet)"])
    out += ["", "## Hand-off file", "", _clip(handoff, 400)]
    return truncate("\n".join(out) + "\n", INJECT_LIMIT)


@beartype
@ensure(lambda result: len(result) == 16 and all(c in "0123456789abcdef" for c in result), "hex")
def fingerprint(snap: Snapshot) -> str:
    """Changes exactly when HEAD, the branch or the working tree's status does: the state file is
    rewritten only then, so the hook after a read-only command costs one ``git status``."""
    # crosshair: per_condition_timeout=2
    h = hashlib.sha256()
    for part in (snap.root, snap.branch, snap.head, *snap.status, *snap.diffstat):
        h.update(part.encode("utf-8", "replace") + b"\0")
    return h.hexdigest()[:16]


# Secret-shaped text, by the published shapes of the tokens this project's people hold or could
# paste (Anthropic, OpenAI, GitHub, GitLab, AWS, Google, Hugging Face, Slack, Stripe, npm, JWTs,
# PEM keys), credentials inside a URL, and assignments (DB_PASSWORD=..., "api_key": "...").
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[rs]k_live_[A-Za-z0-9]{10,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{20,}=*"),
)
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+:)(?!\[redacted\])[^@\s/]+(@)")
# The value must hold a letter, so a count such as max_tokens: 100000 stays.
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*"
    r"[\"']?\s*[:=]\s*[\"']?)(?!\[redacted\])(?=[^\s'\"`|]*[A-Za-z])[^\s'\"`|]{6,}"
)
REDACTED = "[redacted]"


@beartype
@ensure(lambda result, text: result[1] > 0 or result[0] == text, "nothing found, nothing changed")
@ensure(lambda result: result[1] >= 0, "a count")
def redact(text: str) -> tuple[str, int]:
    """Replace secret-shaped strings with ``[redacted]``; the count says how many. A hand-off file
    travels between accounts and machines, so it never carries one."""
    # crosshair: per_condition_timeout=3
    count = 0
    for pattern in _SECRET_PATTERNS:
        text, n = pattern.subn(REDACTED, text)
        count += n
    text, n = _URL_CREDENTIALS.subn(lambda m: m.group(1) + REDACTED + m.group(2), text)
    count += n
    text, n = _ASSIGNMENT.subn(lambda m: m.group(1) + REDACTED, text)
    return text, count + n


def _heading_section(title: str) -> tuple[int, bool]:
    """(section number, matched by number only) for one heading's text, 0 when it is none."""
    low = title.lower()
    for number, words in _SECTION_WORDS.items():
        if any(w in low for w in words):
            return number, False
    m = re.match(r"\s*(\d)[.)]", title)
    if m and 1 <= int(m.group(1)) <= 6:
        return int(m.group(1)), True
    return 0, False


def _coerce_phase(raw: str) -> tuple[str, str]:
    """(phase, repair note) from whatever the writer put after ``Phase:``."""
    cleaned = raw.strip().strip("*[]").strip()
    for phase in PHASES:
        if cleaned.lower() == phase.lower():
            return phase, ""
    for phase in PHASES:
        if cleaned[:5].lower() == phase[:5].lower() and cleaned:
            return phase, f"phase '{_clip(cleaned, 40)}' read as {phase}"
    if not cleaned:
        return "Unknown", "no phase given"
    return "Unknown", f"phase '{_clip(cleaned, 40)}' is none of {', '.join(PHASES)}"


def _coerce_status(raw: str) -> tuple[str, str]:
    cleaned = raw.strip().strip("*`[]").strip()
    for status in _FILE_STATUSES:
        if cleaned.lower()[:4] == status.lower()[:4] and cleaned:
            note = "" if cleaned == status else f"status '{_clip(cleaned, 30)}' read as {status}"
            return status, note
    return _clip(cleaned, 30) or "Unknown", f"status '{_clip(cleaned, 30)}' is not one of three"


@beartype
@ensure(lambda result, text: result.size == len(text.encode("utf-8")), "the size is the file's")
@ensure(
    lambda result: (
        set(result.present).isdisjoint(result.missing)
        and set(result.present) | set(result.missing) == {1, 2, 3, 4, 5, 6}
    ),
    "every section is either found or reported missing",
)
@ensure(
    lambda result: not result.ok or (not result.missing and result.next_actions >= 1),
    "a usable file has all six sections and at least one next action",
)
def parse_context(text: str) -> ContextReport:
    """Read a hand-off file coercively (schema-aligned parsing, CLAUDE.md step 5): headings by
    keyword or by number, the phase and the file statuses coerced to the schema's words, every
    repair reported, never a hard failure on a formatting slip."""
    # crosshair: per_condition_timeout=3
    lines = text.replace("\r\n", "\n").split("\n")
    repairs: list[str] = []
    warnings: list[str] = []
    bodies: dict[int, list[str]] = {}
    current = 0
    fenced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced  # a shell comment inside a fence is code, not a heading
        elif not fenced and stripped.startswith("#"):
            title = stripped.lstrip("#").strip().strip("*").strip()
            number, by_number = _heading_section(title)
            if number:
                if number in bodies:
                    repairs.append(f"section {number} appears twice; the parts were joined")
                if by_number:
                    repairs.append(f"section {number} recognised by its number only")
                bodies.setdefault(number, [])
                current = number
                continue
            if "serialization" in title.lower() or "serialisation" in title.lower():
                current = 0
                continue
        if current:
            bodies[current].append(line)
    head = "\n".join(lines[:12])
    ts = re.search(r"Timestamp:\**\s*([^*\n]+?)\s*(?:\*\*|Phase:|$)", head, re.M)
    ph = re.search(r"Phase:\**\s*([^*\n]+?)\s*(?:\*\*|$)", head, re.M)
    timestamp = ts.group(1).strip() if ts else ""
    if not timestamp:
        repairs.append("no timestamp given")
    phase, note = _coerce_phase(ph.group(1) if ph else "")
    if note:
        repairs.append(note)
    files: list[tuple[str, str]] = []
    for line in bodies.get(3, []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not line.strip().startswith("|"):
            continue
        if set("".join(cells)) <= set("-: ") or cells[0].lower().strip("*") == "file path":
            continue
        status, note = _coerce_status(cells[1])
        if note:
            repairs.append(note)
        files.append((_clip(cells[0].strip("`*"), 200), status))
    section6 = bodies.get(6, [])
    listed = sum(1 for line in section6 if re.match(r"\s*(?:\d+[.)]|[-*])\s+\S", line))
    inline = len(re.findall(r"(?:^|\s)\d+[.)]\s+\S", "\n".join(section6)))
    actions = max(listed, inline)
    present = tuple(sorted(bodies))
    missing = tuple(n for n, _ in SECTIONS if n not in bodies)
    size = len(text.encode("utf-8"))
    if size > CONTEXT_WARN_BYTES:
        warnings.append(f"{size} bytes: above {CONTEXT_WARN_BYTES}, the next session pays for it")
    if actions and not 3 <= actions <= 5:
        warnings.append(f"{actions} next actions; the schema asks for 3 to 5")
    if 3 in bodies and not files:
        warnings.append("section 3 has no table rows")
    return ContextReport(
        ok=not missing and actions >= 1,
        phase=phase,
        timestamp=_clip(timestamp, 60),
        present=present,
        missing=missing,
        files=tuple(files[:200]),
        next_actions=actions,
        repairs=tuple(repairs),
        warnings=tuple(warnings),
        size=size,
    )


@beartype
@ensure(lambda result: result.startswith("hand-off file"), "one line about the file")
@ensure(lambda result: "\n" not in result, "one line")
def describe_context(report: ContextReport | None, path: str, written: str) -> str:
    """The one line that rides in the state file and the injection about the hand-off file."""
    # crosshair: per_condition_timeout=2
    where = _clip(path.replace("\n", " "), 200)
    if report is None:
        return f"hand-off file {where}: absent; write it with /handoff before this account stops"
    state = "complete" if report.ok else f"missing sections {list(report.missing)}"
    extra = f"; {len(report.repairs)} repairs" if report.repairs else ""
    return (
        f"hand-off file {where}: written {_clip(written.replace(chr(10), ' '), 30)}, phase "
        f"{report.phase}, {state}, {report.next_actions} next actions, {report.size} bytes{extra}"
    )


@beartype
@require(lambda context_path: context_path != "", "where the file goes")
@ensure(lambda result: "## 6. Immediate Next Actions" in result, "the schema rides along")
def extraction_prompt(context_path: str, web: bool) -> str:
    """The guide's extraction mega-prompt. ``web`` is the form greg pastes into claude.ai himself
    as the last message there; the other is what ``/handoff`` and the Stop gate point a session
    at. Nothing of ours runs on claude.ai."""
    # crosshair: per_condition_timeout=2
    if web:
        where = (
            "Output only the file, inside one fenced markdown block, nothing before or after it. I "
            "will save it and run `bee handoff import` on it."
        )
    else:
        where = f"Write it to `{context_path}` now with your Write tool, replacing what is there."
    return (
        "# Write the hand-off file\n\n"
        "Serialise this session's working state into a hand-off file in exactly the schema below. "
        "It is what the next session reads if this one stops: a usage limit, a switch of account, "
        "another machine. "
        f"{where}\n\n"
        "Rules:\n\n"
        "- Exactly these headings, in this order. No conversation, no pleasantries.\n"
        "- Paths are relative to the project root and exist on disk now; check with `ls` or "
        "`git status` before you list one. Never describe a file you only planned.\n"
        "- Section 5 copies exact error strings, never a summary of them, then your hypothesis. "
        "Write `None` when nothing is blocked.\n"
        "- Section 6 has 3 to 5 numbered imperative steps the next session can run at once.\n"
        f"- Keep it under {CONTEXT_WARN_BYTES // 1024} KB. Never put a secret, token, password or "
        "key in it.\n\n"
        "```markdown\n" + SCHEMA + "```\n"
    )


@beartype
@require(lambda context_path, state_path: context_path != "" and state_path != "", "two paths")
@ensure(lambda result: "Filesystem validation" in result, "the critical step is always there")
def hydration_prompt(context_path: str, state_path: str, extract_path: str) -> str:
    """The guide's hydration prompt, as ``HYDRATE.md``: read, validate against the disk, verify the
    blockers, carry the decisions into CLAUDE.md, then act, and only then ask."""
    # crosshair: per_condition_timeout=2
    return (
        "# Continue from the hand-off\n\n"
        "Another Claude Code session, possibly on another account or machine, stopped and left "
        "this work to you. Do this, in order:\n\n"
        f"1. Read `{context_path}`, the hand-off file. If it is absent, say so and work from "
        f"`{state_path}` and the project's CLAUDE.md.\n"
        "2. **Filesystem validation (critical).** Do not trust the hand-off blindly. For each row "
        'of "Files Modified & State", confirm with targeted checks (`git status`, `git log -3`, '
        "`grep -n` for the one symbol, `ls`) that the file exists and is in the stated state. "
        "Read only the lines you need, never whole files, so this costs little.\n"
        '3. **Blocker verification.** For each item under "Active Blockers & Errors", run the '
        "narrowest command that shows it (the one failing test, the one build step) and compare "
        "the output with the recorded string, exactly.\n"
        '4. **Memory synchronisation.** If a long-term decision under "Architectural Decisions & '
        "Constraints\" is missing from the project's CLAUDE.md, append it there under the heading "
        '"Decisions carried by hand-off" and commit that change on its own.\n'
        '5. **Execute** the "Immediate Next Actions" in order.\n'
        "6. Do not ask for further instructions until those actions are done or you meet a blocker "
        "the hand-off did not foresee.\n\n"
        f"`{state_path}` is generated from git; where it or the disk disagrees with the hand-off, "
        "the disk wins and you say so. After each meaningful unit of work, refresh the hand-off "
        f"file as `{extract_path}` describes.\n"
    )


@beartype
@ensure(
    lambda result, gate_on, stop_hook_active: (
        result == ("", "") or (gate_on and not stop_hook_active)
    ),
    "the gate speaks only when it is on, and never twice in a row",
)
@ensure(lambda result: (result[0] == "") == (result[1] == ""), "a message always says its kind")
@ensure(
    lambda result, used_pct, threshold, asked_threshold: (
        result[1] != "threshold" or (used_pct >= threshold and not asked_threshold)
    ),
    "the threshold is raised once per session, and only at or past it",
)
@ensure(
    lambda result, head, context_head, asked_head: (
        result[1] != "commit" or (head != "" and head != context_head and head != asked_head)
    ),
    "a stale file is raised once per commit",
)
def gate_feedback(
    gate_on: bool,
    stop_hook_active: bool,
    head: str,
    context_head: str,
    context_exists: bool,
    asked_head: str,
    used_pct: int,
    threshold: int,
    asked_threshold: bool,
    extract_path: str,
) -> tuple[str, str]:
    """What the Stop gate tells Claude before it stops and why (``threshold`` or ``commit``), or
    ``("", "")`` to let it stop.

    It asks for a fresh hand-off file when a commit landed since the file was written (once per
    commit), and when the account's window crosses the threshold (once per session), so that the
    file is current before the limit, when the session can no longer write anything.
    """
    # crosshair: per_condition_timeout=4
    if not gate_on or stop_hook_active:
        return ("", "")
    how = f"Write it now as `{_clip(extract_path, 200)}` says, then finish."
    if used_pct >= threshold and not asked_threshold:
        return (
            f"This account's five-hour window reads {used_pct}% (the hand-off threshold is "
            f"{threshold}%). Refresh the hand-off file before it runs out: once the limit is hit "
            f"this session can write nothing. {how}",
            "threshold",
        )
    if head and head != context_head and head != asked_head:
        reason = "you committed since it was written" if context_exists else "it does not exist"
        return (
            f"The hand-off file is stale ({reason}). It is what the next account reads if this "
            f"one stops. {how}",
            "commit",
        )
    return ("", "")


@beartype
@require(lambda threshold: 1 <= threshold <= 100, "a percentage")
@ensure(lambda result: "\n" not in result and len(result) <= 400, "one short line")
def statusline_text(
    label: str,
    five_pct: int,
    five_resets: str,
    seven_pct: int,
    context_pct: int,
    threshold: int,
    color: bool,
    step: str = "/handoff, then exit and run bee continue",
) -> str:
    """The status line: which account, its windows, how full the context is, and what to do at the
    two thresholds (a negative percentage means Claude Code did not send that number; ``step`` is
    what to type at the hand-off threshold, in this machine's words)."""

    # crosshair: per_condition_timeout=3
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    parts = [paint(f"bee:{_flat(label or 'unnamed', 32)}", "1")]
    if five_pct >= 0:
        resets = f" until {_flat(five_resets, 16)}" if five_resets else ""
        text = f"5h {five_pct}%{resets}"
        parts.append(
            paint(text, "31" if five_pct >= threshold else "33" if five_pct >= 75 else "32")
        )
    if seven_pct >= 0:
        parts.append(f"7d {seven_pct}%")
    if context_pct >= 0:
        ctx = f"ctx {context_pct}%"
        parts.append(paint(ctx, "33") if context_pct >= COMPACT_HINT_PCT else ctx)
    if five_pct >= threshold:
        parts.append(paint(_flat(step, 80), "31"))
    elif context_pct >= COMPACT_HINT_PCT:
        parts.append("/compact focus on: the open task and its errors")
    return " · ".join(parts)


# --- input and output -----------------------------------------------------------------------------


def _utc(epoch: float | None = None) -> str:
    stamp = (
        _dt.datetime.now(_dt.UTC) if epoch is None else _dt.datetime.fromtimestamp(epoch, _dt.UTC)
    )
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


@beartype
@ensure(lambda result: os.path.isabs(result), "an absolute folder")
def state_dir(project: str, environ: dict[str, str] | None = None) -> str:
    """``$BEETHOVEN_STATE_DIR``, else the run directory's ``state`` in a job
    (``$BEETHOVEN_RUN_ROOT``), else ``<project>/.claude/state``."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    if env.get("BEETHOVEN_STATE_DIR"):
        return os.path.abspath(env["BEETHOVEN_STATE_DIR"])
    if env.get("BEETHOVEN_RUN_ROOT"):
        return os.path.abspath(os.path.join(env["BEETHOVEN_RUN_ROOT"], "state"))
    return os.path.abspath(os.path.join(project, ".claude", "state"))


def _ensure_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    ignore = os.path.join(path, ".gitignore")
    if not os.path.exists(ignore):
        # The folder is the session's working record, not project code: it ignores itself, so it
        # is never committed by accident in any repository it sits in.
        _write(ignore, "# Beethoven's hand-off state: never committed.\n*\n")


def _write(path: str, text: str, private: bool = False) -> None:
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if os.name == "posix":
            os.chmod(tmp, 0o600 if private else 0o644)
        os.replace(tmp, path)
    except BaseException:
        # Our own temp file in our own state folder, made a moment ago.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _read_json(path: str) -> dict[str, object]:
    raw = _read(path)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _append(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line.replace("\n", " ") + "\n")


def _git(project: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", "-C", project, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
            env={
                **os.environ,
                "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


@beartype
@ensure(lambda result: isinstance(result, Snapshot), "a snapshot, possibly of no repository")
def git_snapshot(project: str) -> Snapshot:
    """Ask git, read-only and without optional locks, what the project's state is."""
    # crosshair: off
    root = _git(project, "rev-parse", "--show-toplevel")
    if root is None:
        return Snapshot(repo=False)
    branch = (_git(project, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    head = (_git(project, "rev-parse", "HEAD") or "").strip()
    status = tuple(x for x in (_git(project, "status", "--porcelain=v1") or "").splitlines() if x)
    diffstat: tuple[str, ...] = ()
    log: tuple[str, ...] = ()
    if head:
        diffstat = tuple((_git(project, "diff", "--stat=150", "HEAD") or "").splitlines())
        log = tuple(
            (
                _git(project, "log", "-n", "8", "--date=short", "--format=%h %ad %s") or ""
            ).splitlines()
        )
    ahead = behind = -1
    counts = _git(project, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if counts and len(counts.split()) == 2:
        ahead, behind = (int(x) for x in counts.split())
    return Snapshot(True, root.strip(), branch, head, ahead, behind, status, diffstat, log)


def _context_line(sdir: str) -> tuple[str, ContextReport | None]:
    path = os.path.join(sdir, CONTEXT_FILE)
    text = _read(path)
    if text is None:
        return describe_context(None, path, ""), None
    report = parse_context(text)
    written = _utc(os.path.getmtime(path))
    return describe_context(report, path, written), report


def _refresh(project: str, env: dict[str, str], force: bool) -> str:
    """Rewrite PROJECT_STATE.md when git's state moved (or when forced); return the state dir."""
    sdir = state_dir(project, env)
    _ensure_dir(sdir)
    snap = git_snapshot(project)
    mark = os.path.join(sdir, ".fingerprint")
    ctx_path = os.path.join(sdir, CONTEXT_FILE)
    ctx_mtime = str(os.path.getmtime(ctx_path)) if os.path.exists(ctx_path) else "none"
    fp = fingerprint(snap) + ":" + ctx_mtime
    if not force and _read(mark) == fp and os.path.exists(os.path.join(sdir, STATE_FILE)):
        return sdir
    line, _ = _context_line(sdir)
    session = _read_json(os.path.join(sdir, SESSION_FILE))
    text, _n = redact(
        render_state(
            snap,
            _utc(),
            env.get("BEE_ACCOUNT", ""),
            str(session.get("session_id") or ""),
            line,
        )
    )
    _write(os.path.join(sdir, STATE_FILE), text)
    _write(mark, fp)
    return sdir


def _validate_context(sdir: str, head: str) -> ContextReport | None:
    """Re-read the hand-off file when it changed, redact it in place if it holds anything
    secret-shaped, and record what was found (``context.json``)."""
    path = os.path.join(sdir, CONTEXT_FILE)
    text = _read(path)
    if text is None:
        return None
    record = _read_json(os.path.join(sdir, CONTEXT_RECORD))
    mtime = os.path.getmtime(path)
    if record.get("mtime") == mtime:
        return parse_context(text)
    clean, hits = redact(text)
    if hits:
        _write(path, clean, private=True)
        mtime = os.path.getmtime(path)
    report = parse_context(clean)
    _write(
        os.path.join(sdir, CONTEXT_RECORD),
        json.dumps(
            {
                "mtime": mtime,
                "head": head,
                "ok": report.ok,
                "phase": report.phase,
                "missing": list(report.missing),
                "next_actions": report.next_actions,
                "repairs": list(report.repairs),
                "warnings": list(report.warnings),
                "redacted": hits,
                "checked": _utc(),
            },
            indent=2,
        )
        + "\n",
    )
    return report


def _prompts(sdir: str) -> None:
    ctx = os.path.join(sdir, CONTEXT_FILE)
    extract = os.path.join(sdir, EXTRACT_FILE)
    _write(extract, extraction_prompt(ctx, web=False))
    _write(
        os.path.join(sdir, HYDRATE_FILE),
        hydration_prompt(ctx, os.path.join(sdir, STATE_FILE), extract),
    )


def _project(payload: dict[str, object], env: dict[str, str]) -> str:
    return str(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def _context_output(event: str, text: str) -> str:
    body = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    return json.dumps(body)


def _has_handoff_command(project: str) -> bool:
    return os.path.isfile(os.path.join(project, ".claude", "commands", "handoff.md"))


def _continue_command(env: dict[str, str]) -> str:
    # bee-grid.sh sets its own (the image has no `bee` for the person to type).
    return _clip(env.get("BEE_CONTINUE_HINT") or "bee continue", 40)


def _next_step(project: str, env: dict[str, str]) -> str:
    write = "/handoff" if _has_handoff_command(project) else "ask for the hand-off file"
    return f"{write}, then exit and run {_continue_command(env)}"


def _session_start(payload: dict[str, object], env: dict[str, str]) -> str:
    project = _project(payload, env)
    sdir = state_dir(project, env)
    _ensure_dir(sdir)
    source = str(payload.get("source") or "startup")
    previous = _read_json(os.path.join(sdir, SESSION_FILE))
    snap = git_snapshot(project)
    record = {
        "session_id": payload.get("session_id"),
        "transcript_path": payload.get("transcript_path"),
        "source": source,
        "account": env.get("BEE_ACCOUNT", ""),
        "cwd": payload.get("cwd"),
        "started": _utc(),
        "head_at_start": snap.head,
        "previous_session": previous.get("session_id"),
    }
    # On a resume or a fork Claude Code reports what re-entering costs (hooks reference,
    # SessionStart); kept for the methods paper's quota record and probe M13.
    for key in ("context_tokens", "estimated_cache_write_usd", "prompt_cache_likely_expired",
                "seconds_since_last_response"):  # fmt: skip
        if key in payload:
            record[key] = payload[key]
    _write(os.path.join(sdir, SESSION_FILE), json.dumps(record, indent=2) + "\n", private=True)
    _append(
        os.path.join(sdir, LOG_FILE),
        f"{_utc()} session-start source={source} account={record['account'] or '-'} "
        f"session={record['session_id']} previous={record['previous_session']} "
        f"context_tokens={record.get('context_tokens', '-')}",
    )
    _prompts(sdir)
    _refresh(project, env, force=True)
    shortcut = "; /handoff does it" if _has_handoff_command(project) else ""
    note = (
        f"Beethoven hand-off: this session's hand-off file is {os.path.join(sdir, CONTEXT_FILE)} "
        f"(how to write it: {os.path.join(sdir, EXTRACT_FILE)}{shortcut})."
    )
    handoff_start = source == "startup" and env.get("BEETHOVEN_HANDOFF") == "1"
    if source in ("resume", "compact", "fork") or handoff_start:
        state = _read(os.path.join(sdir, STATE_FILE)) or ""
        lead = (
            f"Continuing from a hand-off: follow {os.path.join(sdir, HYDRATE_FILE)} before "
            "anything else.\n"
            if handoff_start
            else ""
        )
        return _context_output("SessionStart", truncate(lead + note + "\n\n" + state, INJECT_LIMIT))
    return _context_output("SessionStart", note)


def _post_tool_use(payload: dict[str, object], env: dict[str, str]) -> str:
    project = _project(payload, env)
    sdir = _refresh(project, env, force=False)
    tool_input = payload.get("tool_input")
    target = str(tool_input.get("file_path") or "") if isinstance(tool_input, dict) else ""
    if target and os.path.basename(target) == CONTEXT_FILE:
        _validate_context(sdir, git_snapshot(project).head)
        _refresh(project, env, force=True)
    return ""


def _pre_compact(payload: dict[str, object], env: dict[str, str]) -> str:
    project = _project(payload, env)
    sdir = _refresh(project, env, force=True)
    _validate_context(sdir, git_snapshot(project).head)
    _append(
        os.path.join(sdir, LOG_FILE),
        f"{_utc()} pre-compact trigger={payload.get('trigger', '-')} "
        f"session={payload.get('session_id')}",
    )
    return ""


def _stop(payload: dict[str, object], env: dict[str, str]) -> str:
    project = _project(payload, env)
    _retire_limit(state_dir(project, env), str(payload.get("session_id") or ""))
    if env.get("BEETHOVEN_HANDOFF_GATE") != "1":
        return ""  # no gate, no more work: Stop fires after every turn
    sdir = state_dir(project, env)
    _ensure_dir(sdir)
    head = git_snapshot(project).head
    report = _validate_context(sdir, head)
    record = _read_json(os.path.join(sdir, CONTEXT_RECORD))
    gate = _read_json(os.path.join(sdir, GATE_RECORD))
    usage = _read_json(os.path.join(sdir, USAGE_FILE))
    session = str(payload.get("session_id") or "")
    used = usage.get("five_hour_pct") if usage.get("session_id") == session else None
    threshold = _int(env.get("BEE_THRESHOLD"), DEFAULT_THRESHOLD)
    message, kind = gate_feedback(
        gate_on=env.get("BEETHOVEN_HANDOFF_GATE") == "1",
        stop_hook_active=payload.get("stop_hook_active") is True,
        head=head,
        context_head=str(record.get("head") or ""),
        context_exists=report is not None,
        asked_head=str(gate.get("asked_head") or ""),
        used_pct=used if isinstance(used, int) else -1,
        threshold=threshold,
        asked_threshold=gate.get("asked_threshold_session") == session,
        extract_path=os.path.join(sdir, EXTRACT_FILE),
    )
    if not message:
        return ""
    if kind == "threshold":
        gate["asked_threshold_session"] = session
    else:
        gate["asked_head"] = head
    _write(os.path.join(sdir, GATE_RECORD), json.dumps(gate, indent=2) + "\n")
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": message}}
    )


def _retire_limit(sdir: str, session: str) -> None:
    """Stop fires only on a turn that finished, so a limit this session recorded is over: Claude
    Code waited out the window, or the person typed again after it reset. Left in place, it would
    bench the account at the next bee run and offer a switch for a limit long gone."""
    path = os.path.join(sdir, LIMIT_FILE)
    if not session or not os.path.isfile(path):
        return  # reads only; Stop must not make a state folder in every project
    limit = _read_json(path)
    if limit and not limit.get("handled") and limit.get("session_id") == session:
        limit["handled"] = f"{_utc()} the session went on"
        _write(path, json.dumps(limit, indent=2) + "\n", private=True)


def _stop_failure(payload: dict[str, object], env: dict[str, str]) -> str:
    project = _project(payload, env)
    sdir = _refresh(project, env, force=True)
    error = str(payload.get("error") or "unknown")
    label = env.get("BEE_ACCOUNT", "")
    _append(
        os.path.join(sdir, LOG_FILE),
        f"{_utc()} stop-failure error={error} account={label or '-'} "
        f"session={payload.get('session_id')}",
    )
    if error != "rate_limit":
        return ""
    usage = _read_json(os.path.join(sdir, USAGE_FILE))
    if usage.get("session_id") != payload.get("session_id") or usage.get("label") != label:
        # Another session's reading (the account before, in this project): its reset times are
        # not this account's, so the rest falls back to a whole window.
        usage = {}
    record = {
        "account": label,
        "session_id": payload.get("session_id"),
        "transcript_path": payload.get("transcript_path"),
        "at": _utc(),
        "five_hour_pct": usage.get("five_hour_pct"),
        "five_hour_resets_at": usage.get("five_hour_resets_at", 0),
        "seven_day_pct": usage.get("seven_day_pct"),
        "seven_day_resets_at": usage.get("seven_day_resets_at", 0),
        "details": _clip(str(payload.get("error_details") or ""), 200),
    }
    _write(os.path.join(sdir, LIMIT_FILE), json.dumps(record, indent=2) + "\n", private=True)
    who = f"the account {label}" if label else "this account"
    note = f"Beethoven: {who} reached its usage limit. Exit, then run {_continue_command(env)}."
    return json.dumps({"terminalSequence": f"\033]9;{note}\007"})


def _int(raw: object, default: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def _statusline(payload: dict[str, object], env: dict[str, str]) -> str:
    limits = payload.get("rate_limits")
    limits = limits if isinstance(limits, dict) else {}

    def window(name: str) -> tuple[int, int]:
        w = limits.get(name)
        if not isinstance(w, dict) or w.get("used_percentage") is None:
            return -1, 0
        return round(float(w["used_percentage"])), _int(w.get("resets_at"), 0)

    five, five_reset = window("five_hour")
    seven, seven_reset = window("seven_day")
    ctx_raw = payload.get("context_window")
    ctx = -1
    if isinstance(ctx_raw, dict) and ctx_raw.get("used_percentage") is not None:
        ctx = round(float(ctx_raw["used_percentage"]))
    label = env.get("BEE_ACCOUNT", "")
    workspace = payload.get("workspace")
    project = (
        str(workspace.get("project_dir") or "") if isinstance(workspace, dict) else ""
    ) or str(payload.get("cwd") or os.getcwd())
    if five >= 0 or seven >= 0:
        reading = {
            "label": label,
            "session_id": payload.get("session_id"),
            "five_hour_pct": five if five >= 0 else None,
            "five_hour_resets_at": five_reset,
            "seven_day_pct": seven if seven >= 0 else None,
            "seven_day_resets_at": seven_reset,
        }
        text = json.dumps(reading, indent=2) + "\n"
        targets = [os.path.join(state_dir(project, env), USAGE_FILE)]
        if label:
            registry = env.get("BEE_REGISTRY_DIR") or os.path.join(
                env.get("BEETHOVEN_HOME") or os.path.expanduser("~/.beethoven"), "engine"
            )
            targets.append(os.path.join(registry, "usage", f"{label}.json"))
        for target in targets:
            try:
                if _read(target) == text:
                    continue
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                _write(target, text, private=True)
            except OSError as exc:
                # The reading is a record; the line is what the person sees. Keep the line.
                _log_error(project, env, "statusline", exc)
    resets = ""
    if five_reset:
        resets = _dt.datetime.fromtimestamp(five_reset).strftime("%H:%M")
    return statusline_text(
        label,
        five,
        resets,
        seven,
        ctx,
        _int(env.get("BEE_THRESHOLD"), DEFAULT_THRESHOLD),
        color=env.get("NO_COLOR") is None,
        step=_next_step(project, env),
    )


@beartype
@ensure(lambda result: os.path.isabs(result), "the state folder it refreshed")
def refresh_state(project: str, environ: dict[str, str] | None = None) -> str:
    """Rewrite ``PROJECT_STATE.md`` now and re-check the hand-off file; return the state folder."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    sdir = _refresh(project, env, force=True)
    _validate_context(sdir, git_snapshot(project).head)
    _prompts(sdir)
    return _refresh(project, env, force=True)


@beartype
@ensure(lambda result: result is None or isinstance(result, ContextReport), "a report or nothing")
def context_report(sdir: str) -> ContextReport | None:
    """The hand-off file's report, or None when there is no file."""
    # crosshair: off
    text = _read(os.path.join(sdir, CONTEXT_FILE))
    return None if text is None else parse_context(text)


_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.S)


@beartype
@ensure(lambda result: result[1] >= 0, "a report and the count of strings redacted")
def install_context(sdir: str, text: str) -> tuple[ContextReport, int]:
    """Place a hand-off file written elsewhere (claude.ai, another machine): unwrap one enclosing
    code fence, redact anything secret-shaped, keep the file it replaces as
    ``claude_context.prev.md`` (our own state file), and report what was read."""
    # crosshair: off
    m = _FENCE.match(text)
    body = m.group(1) + "\n" if m else text
    clean, hits = redact(body.replace("\r\n", "\n"))
    _ensure_dir(sdir)
    path = os.path.join(sdir, CONTEXT_FILE)
    old = _read(path)
    if old is not None:
        _write(os.path.join(sdir, "claude_context.prev.md"), old)
    _write(path, clean, private=True)
    _append(os.path.join(sdir, LOG_FILE), f"{_utc()} import bytes={len(clean)} redacted={hits}")
    return parse_context(clean), hits


@beartype
@ensure(lambda result: isinstance(result, dict), "the record, possibly empty")
def read_record(sdir: str, name: str) -> dict[str, object]:
    """One of the state folder's JSON records (``session.json``, ``limit.json``, ...), or {}."""
    # crosshair: off
    return _read_json(os.path.join(sdir, name))


@beartype
@ensure(lambda result: result is None, "written")
def write_record(sdir: str, name: str, record: dict[str, object]) -> None:
    """Write one of the state folder's JSON records, private to its owner."""
    # crosshair: off
    _ensure_dir(sdir)
    _write(os.path.join(sdir, name), json.dumps(record, indent=2) + "\n", private=True)


_HOOKS = {
    "session-start": _session_start,
    "post-tool-use": _post_tool_use,
    "pre-compact": _pre_compact,
    "stop": _stop,
    "stop-failure": _stop_failure,
    "statusline": _statusline,
}


@beartype
@ensure(lambda result: result == 0, "state upkeep never stops a session")
def main(argv: list[str], stdin: _TextStream, stdout: _TextStream) -> int:
    """Run one hook (``argv[0]`` names it) on the event JSON from ``stdin``; print what Claude
    Code should read; answer 0 whatever happens."""
    # crosshair: off
    env = dict(os.environ)
    name = argv[0] if argv else ""
    payload: dict[str, object] = {}
    try:
        raw = stdin.read()
        loaded = json.loads(raw) if raw.strip() else {}
        payload = loaded if isinstance(loaded, dict) else {}
        hook = _HOOKS.get(name)
        if hook is None:
            raise ValueError(f"no hook named {name!r}; one of {', '.join(_HOOKS)}")
        out = hook(payload, env)
        if out:
            stdout.write(out + "\n")
    except Exception as exc:  # noqa: BLE001 - upkeep is best effort; the session goes on
        _log_error(_project(payload, env), env, name, exc)
    return 0


def _log_error(project: str, env: dict[str, str], name: str, exc: Exception) -> None:
    try:
        sdir = state_dir(project, env)
        _ensure_dir(sdir)
        _append(
            os.path.join(sdir, ERROR_LOG),
            f"{_utc()} {name or '-'}: {exc.__class__.__name__}: {_clip(str(exc), 300)}",
        )
    except Exception:  # noqa: BLE001, S110 - nowhere left to write; stay silent and let it run
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], sys.stdin, sys.stdout))
