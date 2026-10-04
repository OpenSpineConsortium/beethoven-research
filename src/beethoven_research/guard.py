"""The PreToolUse guard: the engine's second line against the 2026-09-28 deletion (Linear G4-1).

Claude Code runs this before every Bash, Edit, Write and NotebookEdit call of a headless step: the
call's JSON arrives on stdin, the verdict leaves as an exit code (0 lets the call through, 2 blocks
it with the reason on stderr, which the model reads and routes around instead of retrying variants)
or, for the two borderline cases, as a JSON ``ask`` that a watched session can answer and a
``dontAsk`` batch run turns into a refusal. The managed settings baked into the image name it
(``containers/managed-settings.json``), so no project or user setting can switch it off.

What it refuses is the non-negotiables of CLAUDE.md, written as rules a command string can be
checked against: deletion outside the run directory and node-local temp, anything that touches the
student's sign-in directory or a credential, remote shells and transfers off the grid, scheduler
administration and other people's jobs, history rewrites, privilege and system operations, and
processes that would outlive the job. Permission rules see the start of a command; this sees the
whole string and the resolved path, which is what closes ``xargs rm``, ``rm ./x -rf`` and
``git push origin main --force``.

What it is not: a proof. A determined command can still hide behind an alias or a script it wrote
a moment earlier. The container's three flags, the managed deny rules and this hook are three
lines, not one, and the framework's own refusal under ``CLAUDECODE`` stays the first. The rule
table is deliberately a list of plain regular expressions, each needing one of ``TRIGGER_TOKENS``
as a whole word, so the gate can prove the property that matters as much as blocking: the guard
never refuses a command on account of anything but a listed token or the sign-in directory's own
path (an engine that blocks ``pytest`` is as stalled as one that deletes the workspace).

Any failure inside the guard blocks the call. A hook that crashes exits 1, which Claude Code treats
as a non-blocking error and lets the call through; ``main`` therefore catches everything and
answers 2.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Literal, Protocol, runtime_checkable

from beartype import beartype

# Straight from icontract rather than through beethoven_research.contracts: the image bakes this
# one file at /opt/beethoven-research/hooks/guard.py and runs it before the engine's package is
# bound, so it must stand alone. The gate recognises the contracts by their attributes either way.
from icontract import ensure, require
from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "TRIGGER_TOKENS",
    "Verdict",
    "check_command",
    "check_path",
    "decide",
    "main",
    "normalize_command",
]

# Every refusal in check_command rests on one of these, as a whole word in the normalized command
# (a token with no word characters, like the fork bomb's, is matched as plain text). The gate
# proves it (tools/verify_gate.py) and the tests sample it, rule by rule.
TRIGGER_TOKENS: tuple[str, ...] = (
    "rm", "find", "shred", "xargs", "git",
    "sudo", "su", "doas", "chroot", "mount", "umount", "dd", "fdisk", "parted", "mkfs",
    "shutdown", "reboot", "systemctl", "modprobe", "insmod", "sysctl", "dev",
    "chmod", "chown", "chgrp",
    "nohup", "setsid", "disown", "at", "crontab", "pkill", "killall", "kill", ":()",
    "ssh", "scp", "sftp", "nc", "ncat", "telnet", "socat", "rsync", "curl", "wget",
    "sacctmgr", "scontrol", "scancel", "sbatch", "array",
    "setup", "logout", "login", "purge",
    "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "credentials", "CLAUDE_CONFIG_DIR",
)  # fmt: skip

# Job arrays larger than this need the orchestrator's own fan-out, which is paced against the
# student's quota window (CLAUDE.md, token economy), not a one-line sbatch from inside a step.
MAX_ARRAY = int(os.environ.get("BEETHOVEN_MAX_ARRAY", "200"))

# Roots the agent may write under, besides the project it was started in and the engine's run
# directory for the step: node-local temp (the job binds the node's disk at /tmp) and /dev/shm.
WRITE_ROOTS_ALWAYS: tuple[str, ...] = ("/tmp", "/dev/shm")
# Paths no step edits: the image and the host's system directories.
PROTECTED_ROOTS: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/opt",
    "/var",
    "/boot",
    "/bin",
    "/sbin",
    "/lib",
)

Decision = Literal["allow", "ask", "deny"]


@runtime_checkable
class _TextStream(Protocol):
    """What ``main`` needs of stdin and stdout: the two methods, not a whole ``typing.TextIO``."""

    def read(self) -> str: ...

    def write(self, text: str, /) -> int: ...


class Verdict(BaseModel):
    """What the guard decided and why. ``rule`` is empty exactly when the call is allowed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    rule: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _rule_matches_decision(self) -> Verdict:
        if (self.decision == "allow") != (self.rule == ""):
            raise ValueError("an allowed call carries no rule and a refused one names its rule")
        if self.decision != "allow" and not self.reason:
            raise ValueError("a refusal must say why; the model reads the reason")
        return self


_ALLOW = Verdict(decision="allow")

# Shell wrappers that change nothing about what runs: stripped so the rules see the real command
# (only when a command follows; ``env | sort`` is a command of its own).
_WRAPPERS = re.compile(
    r"^(?:(?:command|exec|time|nice(?: -n \d+)?|timeout [0-9smh]+|env) (?![|;&]))+"
)
_WS = re.compile(r"\s+")
# A command boundary: the start of the string or a separator, with optional spaces after it.
_B = r"(?:^|[;&|]\s*)"
# One simple command's arguments: up to the next separator.
_ARGS = r"[^;&|]*"


@beartype
@ensure(lambda result: "\n" not in result and "  " not in result, "one line, single spaces")
@ensure(lambda result, command: len(result) <= len(command), "only ever shorter")
@ensure(lambda result: result == result.strip(), "no leading or trailing space")
def normalize_command(command: str) -> str:
    """Collapse whitespace and drop leading wrappers (``exec``, ``timeout 30``, ``env``, ...)."""
    collapsed = _WS.sub(" ", command).strip()
    return _WRAPPERS.sub("", collapsed).strip()


def _deny(rule: str, reason: str) -> Verdict:
    return Verdict(decision="deny", rule=rule, reason=reason)


def _ask(rule: str, reason: str) -> Verdict:
    return Verdict(decision="ask", rule=rule, reason=reason)


def _mentions(text: str, token: str) -> bool:
    if token[0].isalnum():
        return re.search(rf"\b{re.escape(token)}\b", text) is not None
    return token in text


def _names_config_dir(text: str, config_dir: str) -> bool:
    # The directory itself or something below it; never a longer name that merely starts the same
    # (/claude is the sign-in directory, /claude-code/... is not).
    return re.search(re.escape(config_dir.rstrip("/")) + r"(?:/|\s|$|[\"'])", text) is not None


def _refusal_is_grounded(command: str, config_dir: str) -> bool:
    c = normalize_command(command)
    return any(_mentions(c, t) for t in TRIGGER_TOKENS) or _names_config_dir(c, config_dir)


# The rule table, in the order the Playbook (greg, 2026-10-03) lists them, plus the engine's own
# credential rules. Each pattern is matched against the normalized command.
_RM_FLAGS = r"-[a-zA-Z]*[rRf][a-zA-Z]*|--recursive|--force"
_COMMAND_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "recursive or forced deletion",
        re.compile(
            rf"{_B}(?:/bin/|/usr/bin/)?rm (?:{_ARGS} )?(?:{_RM_FLAGS})(?: |$)"
            rf"|{_B}find {_ARGS}(?:-delete|-exec +(?:rm|shred))"
            r"|xargs +(?:-[a-zA-Z0-9]+ +)*rm"
            rf"|{_B}git +clean +-[a-zA-Z]*f"
            rf"|{_B}shred(?: |$)"
        ),
        "delete named files one at a time, inside the run directory or node temp; nothing "
        "recursive, nothing forced",
    ),
    (
        "privileged or system operation",
        re.compile(
            rf"{_B}(?:sudo|su|doas|chroot|mount|umount|dd|fdisk|parted|shutdown|reboot|systemctl"
            r"|modprobe|insmod)(?: |;|$)"
            r"|\bmkfs[. ]"
            r"|\bsysctl +-w"
            r"|> */dev/(?:sd|nvme|mem|kmem|hd)"
        ),
        "the container is unprivileged and the image is read-only; no system changes",
    ),
    (
        "recursive permission change",
        re.compile(rf"{_B}(?:chmod|chown|chgrp) +(?:-[a-zA-Z]*R|--recursive)"),
        "change permissions on named files only",
    ),
    (
        "daemon, mass kill or fork bomb",
        re.compile(
            rf"{_B}(?:nohup|setsid|disown|at|crontab|pkill|killall)(?: |$)"
            r"|\bkill +-9 +-1"
            r"|:\(\) *\{ *:\|:& *\};"
        ),
        "background work stays inside this job's allocation and ends with it",
    ),
    (
        "remote shell, transfer off the grid or pipe to shell",
        re.compile(
            rf"{_B}(?:ssh|scp|sftp|nc|ncat|telnet|socat)(?: |$)"
            rf"|\brsync{_ARGS} [^ /]+:"
            r"|\b(?:curl|wget)[^|]*\| *(?:ba|z|da|k)?sh(?: |$)"
        ),
        "data leaves the grid only through the student; download to a file and read it before "
        "running anything",
    ),
    (
        "scheduler administration or other people's jobs",
        re.compile(
            rf"{_B}(?:sacctmgr(?: |$)"
            r"|scontrol +(?:update|hold|release|requeue|reconfigure|cancel|create|delete))"
            rf"|\bscancel {_ARGS}(?:-A|--account[= ]|-p|--partition[= ])"
        ),
        "only scontrol show is permitted, and only your own job ids are cancelled",
    ),
    (
        "credential or sign-in directory",
        re.compile(
            r"\bsetup-token\b"
            r"|\bclaude +auth +(?:logout|login)\b"
            r"|\bclaude +project +purge\b"
            r"|\b(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN)\b"
            r"|\.credentials\.json"
            r"|\bCLAUDE_CONFIG_DIR\b"
        ),
        "Beethoven never reads, prints, copies or relays a Claude credential, never signs the "
        "student in or out and never purges what Claude Code keeps there; claude auth status is "
        "the only question asked of the sign-in",
    ),
    (
        "secret in sbatch --export",
        re.compile(rf"\bsbatch {_ARGS}--export[= ][^ ]*(?:TOKEN|KEY|SECRET|PASSWORD)"),
        "tokens and secrets never travel in sbatch --export, argv or a job file; a per-job secret "
        "is a mode-0600 file",
    ),
    (
        "git history or working-tree destruction",
        re.compile(
            rf"\bgit +(?:push +(?:{_ARGS} )?(?:-f|--force(?:-with-lease)?)(?: |$)"
            r"|reset +--hard|branch +-D|checkout +-- *\.(?: |$)|filter-branch|filter-repo)"
        ),
        "use a new branch or a revert commit; history is the paper's record",
    ),
)

_ARRAY = re.compile(r"--array[= ]+(\d+)-(\d+)")
_SCANCEL_USER = re.compile(rf"\bscancel {_ARGS}(?:-u|--user[= ])")
_SBATCH_WRAP = re.compile(rf"{_B}sbatch {_ARGS}--wrap")
_RM_CALL = re.compile(rf"{_B}(?:/bin/|/usr/bin/)?rm(?: |$)")
# ``rm`` of a root, in any spelling: the whole filesystem, a home, the temp roots, here or above.
_RM_ROOT = re.compile(
    rf"{_B}rm +(?:-[a-zA-Z]+ +)*(?:\"?\$HOME\"?|~|/|/tmp|/dev/shm|\.|\.\.)/?(?: |$)"
)


def _rm_targets(command: str) -> list[str] | None:
    """The non-flag arguments of every ``rm`` in the command, or None if it cannot be read."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    targets: list[str] = []
    for i, word in enumerate(words):
        if word not in ("rm", "/bin/rm", "/usr/bin/rm"):
            continue
        for arg in words[i + 1 :]:
            if arg in (";", "&&", "||", "|", "&"):
                break
            if arg.startswith("-"):
                continue
            targets.append(arg)
    return targets


def _resolve(path: str) -> str | None:
    """``realpath`` that answers None, instead of raising, for a path no filesystem could hold."""
    try:
        return os.path.realpath(path)
    except (ValueError, OSError):
        return None


def _under(path: str, root: str) -> bool:
    """Whether a normalized absolute ``path`` is ``root`` or lies below it."""
    if not root:
        return False
    root = root.rstrip("/")
    if root == "":
        return True  # the root directory holds everything
    return path == root or path.startswith(root + "/")


@beartype
@require(lambda cwd: cwd.startswith("/"), "the working directory is absolute")
@require(lambda config_dir: config_dir.startswith("/"), "the sign-in directory is absolute")
@ensure(
    lambda result, command: command.strip() != "" or result.decision == "allow",
    "an empty command is nothing to refuse",
)
@ensure(
    lambda result, command, config_dir: (
        result.decision == "allow" or _refusal_is_grounded(command, config_dir)
    ),
    "a refusal rests on a listed trigger token or the sign-in directory; nothing else is blocked",
)
def check_command(command: str, cwd: str, run_root: str, config_dir: str) -> Verdict:
    """Judge one shell command string. ``run_root`` may be empty when the step has no run dir."""
    c = normalize_command(command)
    if not c:
        return _ALLOW
    for rule, pattern, reason in _COMMAND_RULES:
        if pattern.search(c):
            return _deny(rule, reason)
    if _names_config_dir(c, config_dir):
        return _deny(
            "credential or sign-in directory",
            "the student's sign-in directory is Claude Code's own; nothing of ours reads it",
        )
    if _RM_ROOT.search(c):
        return _deny("root deletion", "refusing to delete a top-level directory")
    if _RM_CALL.search(c):
        targets = _rm_targets(c)
        if targets is None:
            return _deny("unreadable rm", "the command could not be parsed; write it plainly")
        allowed_roots = (cwd, run_root, *WRITE_ROOTS_ALWAYS)
        for target in targets:
            resolved = os.path.normpath(
                target if target.startswith("/") else os.path.join(cwd, target)
            )
            if not any(_under(resolved, root) for root in allowed_roots):
                return _deny(
                    "deletion outside the run directory",
                    f"{target} is outside the project, the run directory and node temp; nothing "
                    "is deleted anywhere else",
                )
    m = _ARRAY.search(c)
    if m:
        size = int(m.group(2)) - int(m.group(1)) + 1
        if size > MAX_ARRAY:
            return _deny(
                "job array too large",
                f"arrays are capped at {MAX_ARRAY} tasks from inside a step; the orchestrator "
                "paces larger fan-out against the quota window",
            )
    if _SCANCEL_USER.search(c):
        return _ask("scancel by user", "scancel -u would cancel every job you own; confirm")
    if _SBATCH_WRAP.search(c):
        return _ask(
            "sbatch --wrap", "sbatch --wrap skips the job-script file; write a .sbatch file"
        )
    return _ALLOW


@beartype
@require(lambda path: path.startswith("/"), "the tool gives an absolute path")
@require(lambda cwd: cwd.startswith("/"), "the working directory is absolute")
@require(lambda config_dir: config_dir.startswith("/"), "the sign-in directory is absolute")
@ensure(
    lambda result, path, config_dir: (
        not _under(os.path.normpath(path), config_dir) or result.decision == "deny"
    ),
    "a path inside the sign-in directory is never writable",
)
@ensure(
    lambda result, path, cwd, run_root: (
        result.decision == "deny"
        or _under(os.path.normpath(path), cwd)
        or _under(os.path.normpath(path), run_root)
        or any(_under(os.path.normpath(path), root) for root in WRITE_ROOTS_ALWAYS)
    ),
    "a write is allowed only inside the project, the run directory or node temp",
)
def check_path(path: str, cwd: str, run_root: str, config_dir: str) -> Verdict:
    """Judge one file path an Edit, Write or NotebookEdit call wants to change."""
    p = os.path.normpath(path)
    if _under(p, config_dir):
        return _deny(
            "sign-in directory",
            "the student's sign-in directory is Claude Code's own; nothing of ours writes there",
        )
    if any(_under(p, root) for root in PROTECTED_ROOTS):
        return _deny("protected path", f"{p} is the image or the host, not project code")
    if _under(p, cwd) or _under(p, run_root) or any(_under(p, r) for r in WRITE_ROOTS_ALWAYS):
        return _ALLOW
    return _deny(
        "write outside the run directory",
        f"{p} is outside the project, the run directory and node temp",
    )


@beartype
@ensure(
    lambda result, payload: (
        payload.get("tool_name") in ("Bash", "Edit", "Write", "NotebookEdit")
        or result.decision == "allow"
    ),
    "tools the guard does not judge pass through",
)
def decide(payload: dict[str, object], environ: dict[str, str] | None = None) -> Verdict:
    """Judge one PreToolUse event (the JSON Claude Code sends), with the roots from ``environ``.

    ``CLAUDE_CONFIG_DIR`` names the student's sign-in directory (Claude Code's own variable),
    ``BEETHOVEN_RUN_ROOT`` the engine's run directory for this step (set by the job; absent when a
    step has none), ``CLAUDE_PROJECT_DIR`` the project (set by Claude Code for every hook).
    """
    tool = payload.get("tool_name")
    if tool not in ("Bash", "Edit", "Write", "NotebookEdit"):
        return _ALLOW
    env: dict[str, str] | os._Environ[str] = os.environ if environ is None else environ
    cwd = str(payload.get("cwd") or env.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    config_dir = _resolve(env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))
    run_root_name = env.get("BEETHOVEN_RUN_ROOT", "")
    run_root = _resolve(run_root_name) if run_root_name else ""
    if (
        not cwd.startswith("/")
        or config_dir is None
        or not config_dir.startswith("/")
        or run_root is None
    ):
        # Roots the guard cannot place are not a licence to proceed. (CrossHair found the first
        # version crashing on a run root no filesystem could hold; a crash would have let the
        # call through as a non-blocking hook error.)
        return _deny(
            "unreadable roots",
            "the working, sign-in or run directory could not be resolved; nothing is judged",
        )
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool == "Bash":
        return check_command(str(tool_input.get("command") or ""), cwd, run_root, config_dir)
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return _ALLOW
    path = str(raw)
    if not path.startswith("/"):
        path = os.path.join(cwd, path)
    # Resolve links at the boundary so a link into the sign-in directory is seen for what it is;
    # the pure check below then needs no filesystem.
    resolved = _resolve(path)
    if resolved is None:
        return _deny("unreadable path", "the path could not be resolved; nothing is written there")
    return check_path(resolved, cwd, run_root, config_dir)


@beartype
@ensure(lambda result: result in (0, 2), "0 lets the call through, 2 blocks it; nothing else")
def main(stdin: _TextStream, stdout: _TextStream, stderr: _TextStream) -> int:
    """Read one event from ``stdin``, write the verdict, return the exit code Claude Code reads."""
    try:
        payload = json.loads(stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        verdict = decide(payload)
    except Exception as exc:  # noqa: BLE001 - fail closed: an unjudged call never goes through
        stderr.write(
            f"beethoven guard: could not judge the call ({exc.__class__.__name__}); blocked\n"
        )
        return 2
    if verdict.decision == "allow":
        return 0
    if verdict.decision == "ask":
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": verdict.reason,
                }
            },
            stdout,
        )
        stdout.write("\n")
        return 0
    stderr.write(f"beethoven guard: blocked ({verdict.rule}). {verdict.reason}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin, sys.stdout, sys.stderr))
