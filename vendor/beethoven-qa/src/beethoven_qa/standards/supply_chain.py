"""Supply chain: the GitHub Actions workflows that build, test and merge the code.

The Drive file's OpenSSF Scorecard section: a flawless codebase means little if the pipeline that
forges it can be subverted. Scorecard itself scores a whole repository against GitHub's API and
counts human review, which this project has chosen not to require (greg, 2026-10-04: the checks
alone decide a merge); so its checks that read the workflow files are applied here, to every file
under ``.github/workflows/``, the way Scorecard defines them:

- **Pinned-Dependencies**: every action a workflow uses, step or reusable workflow, is pinned to a
  full 40-character commit, never a tag or a branch that can be moved; a local action
  (``./path``) and a container pinned by its ``sha256`` digest are pinned. A download handed to a
  shell or an interpreter to run (``curl ... | sh``, ``| python3``, ``bash <(curl ...)``,
  ``eval "$(curl ...)"``) runs whatever the server sends that day and is refused; so is one piped
  into an unpacker, and a file a step fetches with curl or wget (``-o``, ``-O``, ``>``, wget's own
  naming, ``| tee``) that its job then runs, installs, unpacks, chmods or puts beside the commands
  before a sha256 check names it (``sha256sum -c`` or ``shasum``, over that file or the line
  piped into it, its failure not thrown away with ``|| true``), in that step or a later one of the
  job. The script is read as the shell commands it holds, with their quotes and without their
  comments, in order (``_commands``), so a digest in a comment, a check of another file or one
  that comes after the use is none.
- **Token-Permissions**: every workflow declares top-level ``permissions`` that grant no write; a
  job that needs to write says so on the job.
- **Dangerous-Workflow**: a workflow started by ``pull_request_target`` or ``workflow_run`` runs
  with the repository's secrets and write token, so it never checks out the pull request's code;
  and no ``run`` script expands an expression an outsider controls (an issue's or a pull request's
  title or body, a branch name, a commit message), which would let that text run as shell.

Beside this reader, zizmor (the established auditor of workflow files) runs over the same files at
its regular persona, offline, with its ignore comments and configuration files not honoured
(``--no-ignores --no-config``: an inline ``zizmor: ignore[...]`` comment or a ``zizmor.yml``
silences and loosens nothing, the rule every group keeps). Each of its findings is a result of its
own run in the
group's log, ``zizmor/<audit>``, at the line of the finding's primary location and anchored to
that line's text; one of **High or Medium severity** is an error and blocks when the change made
it, a Low or Informational one is a note. Measured 2026-10-08 with zizmor 1.30.1: its JSON counts
rows from 0 where its plain output and GitHub count lines from 1; a file it cannot read as a
workflow it passes over with a warning, as the reader passes over one that does not parse; and on
this repository's own workflows it reports one Low, ``adhoc-packages`` for the ``npm install -g``
of the Claude Code CLI, which has no lockfile to pin it (recorded, accepted).

Scorecard's other checks (branch protection, code review, signed releases, a security policy and
others) read GitHub's settings, not files; this repository's rulesets as code
(``.github/rulesets/main.json``) are its own record of the first. Scorecard itself runs over the
tree in CI as a report that never blocks (the job ``scorecard (report)`` of ``standards.yml``;
``docs/STANDARDS.md`` says why).
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import NamedTuple

import yaml
from beartype import beartype
from pydantic import BaseModel, ConfigDict

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import Finding, blocking
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _bin,
    _compare,
    _tool,
    _version,
)
from beethoven_qa.standards.sarif import Rule, Run

__all__ = [
    "BLOCKING",
    "GROUP",
    "RULES",
    "grants_write",
    "level_for",
    "pinned",
    "pipes_to_shell",
    "unchecked_download",
    "untrusted",
]

GROUP = Group(
    "supply-chain",
    "the workflows that build, test and merge",
    "OpenSSF Scorecard's checks of workflow files, and zizmor's audits",
)

WORKFLOWS = ".github/workflows"
BLOCKING = ("Medium", "High")  # zizmor's severities that make a finding an error
_SCORECARD = "https://github.com/ossf/scorecard/blob/main/docs/checks.md"
_ZIZMOR = "https://docs.zizmor.sh/audits/"
_TAGS = ("security", "supply-chain")


def _rule(key: str, name: str, text: str, check: str) -> Rule:
    return Rule(
        f"supply-chain/{key}",
        name,
        text,
        f"OpenSSF Scorecard, {check}.",
        f"{_SCORECARD}#{check.lower()}",
        _TAGS,
    )


RULES: dict[str, Rule] = {
    r.id: r
    for r in (
        _rule(
            "pinned-action",
            "PinnedAction",
            "An action is pinned to a full commit.",
            "Pinned-Dependencies",
        ),
        _rule(
            "pipe-to-shell",
            "PipeToShell",
            "No download is piped into a shell or an interpreter, or substituted into its "
            "command line.",
            "Pinned-Dependencies",
        ),
        _rule(
            "unchecked-download",
            "UncheckedDownload",
            "A file a step fetches and that its job then runs, installs, unpacks, chmods or puts "
            "beside the commands is checked against a sha256 digest first; no download is piped "
            "into an unpacker.",
            "Pinned-Dependencies",
        ),
        _rule(
            "token-permissions",
            "TokenPermissions",
            "A workflow declares top-level permissions that grant no write.",
            "Token-Permissions",
        ),
        _rule(
            "untrusted-checkout",
            "UntrustedCheckout",
            "A pull_request_target or workflow_run workflow never checks out the pull request.",
            "Dangerous-Workflow",
        ),
        _rule(
            "script-injection",
            "ScriptInjection",
            "No run script expands an expression an outsider controls.",
            "Dangerous-Workflow",
        ),
    )
}

_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
# --- the shell a step runs ------------------------------------------------------------------------
# A run script is read as the shell commands it holds: with their quotes (a `&` or a `;` inside a
# quoted URL separates nothing), their comments dropped, a backslash-newline joined, in order; a
# parenthesised run (a subshell, a command or process substitution) is one word, "$(...)", of the
# command it belongs to. shlex does the reading, a line at a time.
_OPERATOR = re.compile(r"\|\||&&|[|&;()]")
_OPERATORS = frozenset({"|", "||", "&&", ";", "&"})
_ASSIGNMENT = re.compile(r"\w+=")
_FETCH_WORD = re.compile(r"\b(?:curl|wget)\b")
# What only wraps a command: the shell's keywords and negation, sudo and the like, VAR=value.
_WRAPPERS = frozenset(
    {"sudo", "env", "nice", "time", "exec", "command", "if", "then", "else", "elif", "fi"}
    | {"while", "until", "do", "done", "!", "{", "}"}
)
_FETCHERS = frozenset({"curl", "wget"})
# What runs what it is given: a shell, an interpreter, or the shell's own ways of reading a file.
_INTERPRETERS = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "python", "python2", "python3", "perl", "node", "ruby"}
    | {"php", "eval", "source", "."}
)
_UNPACKERS = frozenset({"tar", "unzip", "gunzip", "unxz", "xz", "bsdtar", "bunzip2", "zcat"})
_MOVERS = frozenset({"mv", "cp"})
# What trusts a fetched file's content: running it, through an interpreter or by its own name,
# making it executable, unpacking it, installing it, or moving or copying it beside the commands
# (a path holding "bin").
_USERS = (
    _INTERPRETERS
    | _UNPACKERS
    | _MOVERS
    | frozenset({"chmod", "install", "pip", "pip3", "pipx", "npm", "npx", "dpkg", "rpm", "apk"})
    | frozenset({"apt", "apt-get"})
)
_HASHERS = ("sha256sum", "shasum")
_SUBSTITUTION = re.compile(r"\$\(\s*(?:sudo\s+)?(?:curl|wget)\b")


class _Command(NamedTuple):
    before: str  # the operator before it: "" at the start, else "|", "||", "&&", ";" or "&"
    words: tuple[str, ...]


def _tokens(script: str) -> list[str]:
    # shlex over each line (a backslash-newline joined first), with ";" between lines: words with
    # their quotes removed, operators apart (a run of them split), comments dropped. A line shlex
    # cannot read (an unclosed quote) stands as one word.
    found: list[str] = []
    for line in script.replace("\\\n", " ").split("\n"):
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            words = list(lexer)
        except ValueError:
            words = [line.strip()]
        for word in words:
            found += _OPERATOR.findall(word) if re.fullmatch(r"[|&;()]+", word) else [word]
        found.append(";")
    return found


def _grouped(tokens: list[str]) -> list[str]:
    # Each parenthesised run folded into one word, "$(...)", so a subshell, a command or a process
    # substitution is a word of the command it belongs to; an unclosed one stands word by word.
    found: list[str] = []
    inner: list[str] = []
    depth = 0
    for token in tokens:
        if token == "(":
            depth += 1
            inner += [token] if depth > 1 else []
        elif token == ")" and depth:
            depth -= 1
            if depth:
                inner.append(token)
            else:
                found.append(f"$({' '.join(inner)})")
                inner = []
        elif depth:
            inner.append(token)
        else:
            found.append(token)
    return found + inner


def _commands(script: str) -> list[_Command]:
    # The commands of a script in order, each with the operator before it; an operator with no
    # command before it waits for the next (a pipe ending a line, its command on the next).
    found: list[_Command] = []
    words: list[str] = []
    before = ""
    for token in _grouped(_tokens(script)):
        if token not in _OPERATORS:
            words.append(token)
        elif words:
            found.append(_Command(before, tuple(words)))
            words, before = [], token
        elif token != ";":
            before = token
    return found + ([_Command(before, tuple(words))] if words else [])


def _bare(words: tuple[str, ...]) -> tuple[str, ...]:
    # The command without what only wraps it: the shell's keywords, sudo and the like with their
    # options, VAR=value assignments.
    rest = list(words)
    while rest and (rest[0] in _WRAPPERS or rest[0].startswith("-") or _ASSIGNMENT.match(rest[0])):
        rest.pop(0)
    return tuple(rest)


def _head(words: tuple[str, ...]) -> str:
    # The name of the command, its directory dropped (/usr/bin/bash is bash, ./tool is tool).
    bare = _bare(words)
    return bare[0].rsplit("/", 1)[-1] if bare else ""


def _url_name(args: tuple[str, ...]) -> str:
    # The name a remote file is kept under when the fetch gives none: the last component of the
    # URL's path, its query dropped.
    url = next((a for a in args if "://" in a), args[-1] if args else "")
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _redirected(args: tuple[str, ...]) -> str | None:
    # The file the command's output is redirected to, `> f` or `>f`; None when it is not.
    for i, arg in enumerate(args):
        if arg.startswith(">") and not arg.startswith(">&"):
            return arg.lstrip(">") or (args[i + 1] if i + 1 < len(args) else "")
    return None


def _short(arg: str, letter: str) -> bool:
    # Whether a one-dash option cluster ends with the letter (-o, -fsSLo).
    return arg.startswith("-") and not arg.startswith("--") and arg.endswith(letter)


def _curl_output(args: tuple[str, ...]) -> str:
    # Where curl writes: the file -o or --output names (`-` is its stdout), the remote name under
    # -O or --remote-name, else its stdout ("").
    for i, arg in enumerate(args):
        following = args[i + 1] if i + 1 < len(args) else ""
        if arg == "--output" or _short(arg, "o"):
            return "" if following == "-" else following
        remote = arg.startswith("-") and not arg.startswith("--") and "O" in arg
        if arg == "--remote-name" or remote:
            return _url_name(args)
    return ""


def _wget_named(arg: str, following: str) -> str | None:
    # The output a wget option names: the file, "-" for its stdout (-O -, -O-, -qO-,
    # --output-document=-), None when the option names none.
    if arg.startswith("--output-document="):
        return arg.split("=", 1)[1]
    if arg == "--output-document" or _short(arg, "O"):
        return following
    return "-" if _short(arg, "O-") else None


def _wget_output(args: tuple[str, ...]) -> str:
    # Where wget writes: the file an option names, its stdout (""), else the remote name.
    for i, arg in enumerate(args):
        named = _wget_named(arg, args[i + 1] if i + 1 < len(args) else "")
        if named is not None:
            return "" if named == "-" else named
    return _url_name(args)


def _fetched(words: tuple[str, ...]) -> str | None:
    # The file a curl or wget command writes, "" when it writes to its stdout, None when the
    # command is no fetch.
    head, args = _head(words), _bare(words)[1:]
    if head not in _FETCHERS:
        return None
    redirected = _redirected(args)
    if redirected is not None:
        return redirected
    return _curl_output(args) if head == "curl" else _wget_output(args)


def _names(words: tuple[str, ...], file: str) -> bool:
    # Whether a command names the fetched file: its last component as a word of its own or at
    # the end of a path, inside a quoted argument too ("<digest>  /usr/local/bin/pixi").
    base = re.escape(file.rsplit("/", 1)[-1])
    pattern = re.compile(rf"(?<![\w.-]){base}(?![\w.-])")
    return any(pattern.search(word) for word in words)


def _piped_into(commands: list[_Command], i: int) -> str:
    # The name of the command that reads command i's output through a pipe, "" when none does.
    after = commands[i + 1] if i + 1 < len(commands) else None
    return _head(after.words) if after is not None and after.before == "|" else ""


def _checks(commands: list[_Command], i: int, file: str) -> bool:
    # Whether command i checks the file's digest: sha256sum or shasum run with the file named, in
    # it or in the command piped into it, and its failure not thrown away (`|| true`).
    words = commands[i].words
    hashing = any(hasher in word for hasher in _HASHERS for word in words)
    piped = commands[i].before == "|" and i > 0 and _names(commands[i - 1].words, file)
    after = commands[i + 1] if i + 1 < len(commands) else None
    thrown = after is not None and after.before == "||" and _head(after.words) in {"true", ":"}
    return hashing and (_names(words, file) or piped) and not thrown


def _uses(words: tuple[str, ...], file: str) -> bool:
    # Whether a command trusts the fetched file's content (_USERS): mv and cp only when they put
    # it beside the commands, a path holding "bin".
    head = _head(words)
    if head and head == file.rsplit("/", 1)[-1]:
        return True
    if head in _MOVERS:
        return _names(words, file) and any("bin" in word.lower() for word in words[1:])
    return head in _USERS and _names(words, file)


def _used_first(commands: list[_Command], i: int, file: str) -> bool:
    # Whether the file command i fetched is used before any command checks its digest.
    for j in range(i + 1, len(commands)):
        if _checks(commands, j, file):
            return False
        if _uses(commands[j].words, file):
            return True
    return False


def _unchecked(commands: list[_Command], i: int) -> bool:
    # Whether command i fetches something the job trusts unchecked: a file it goes on to use
    # before a digest check names it (`| tee f` makes a file), or a stream piped into an unpacker.
    file = _fetched(commands[i].words)
    if file is None:
        return False
    into = _piped_into(commands, i)
    if file == "" and into == "tee":
        args = _bare(commands[i + 1].words)[1:]
        file = next((word for word in args if not word.startswith("-")), "")
    if file == "":
        return into in _UNPACKERS
    return _used_first(commands, i, file)


_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
# GitHub's own list of untrusted input, from its security hardening guide for Actions.
_UNTRUSTED = re.compile(
    r"\bgithub\s*\.\s*(head_ref\b|event\s*\.\s*("
    r"(issue|pull_request|discussion)\s*\.\s*(title|body)\b"
    r"|(comment|review|review_comment)\s*\.\s*body\b"
    r"|pages\s*\.[^.\s]+\.\s*page_name\b"
    r"|(commits\s*\.[^.\s]+|head_commit|workflow_run\s*\.\s*head_commit)\s*\.\s*"
    r"(message|author\s*\.\s*(email|name))\b"
    r"|pull_request\s*\.\s*head\s*\.\s*(ref|label|repo\s*\.\s*default_branch)\b"
    r"|workflow_run\s*\.\s*head_branch\b))"
)


@beartype
@ensure(
    lambda result, uses: (
        not result
        or uses.startswith("./")
        or (_DIGEST.fullmatch(uses) is not None)
        or (_SHA.fullmatch(uses.rsplit("@", 1)[-1]) is not None and "@" in uses)
    ),
    "pinned means a local path, a container by digest, or a full commit after the @",
)
def pinned(uses: str) -> bool:
    """Whether the ``uses:`` value of a step or a job names one fixed thing."""
    if uses.startswith("./"):
        return True
    if uses.startswith("docker://"):
        return _DIGEST.fullmatch(uses) is not None
    name, at, ref = uses.rpartition("@")
    return bool(name) and bool(at) and _SHA.fullmatch(ref) is not None


@beartype
@ensure(
    lambda result, permissions: (
        result
        == (
            permissions == "write-all"
            or (isinstance(permissions, dict) and "write" in permissions.values())
        )
    ),
    "a write is write-all, or a scope set to write",
)
def grants_write(permissions: str | dict[str, str] | None) -> bool:
    """Whether a ``permissions:`` value grants any write."""
    if isinstance(permissions, dict):
        return "write" in permissions.values()
    return permissions == "write-all"


@beartype
@ensure(
    lambda result, script: all(f"${{{{{e}}}}}" in script for e in result),
    "every expression reported is one the script holds",
)
@ensure(lambda result: all(_UNTRUSTED.search(e) for e in result), "and it reads untrusted input")
def untrusted(script: str) -> tuple[str, ...]:
    """The ``${{ }}`` expressions in ``script`` that read input an outsider controls."""
    return tuple(e for e in _EXPRESSION.findall(script) if _UNTRUSTED.search(e))


@beartype
@require(lambda script: "\0" not in script, "a script holds no NUL")
@ensure(
    lambda result, script: not result or _FETCH_WORD.search(script) is not None,
    "a report needs a curl or wget",
)
@ensure(lambda result, script: not result or "|" in script or "(" in script, "piped or substituted")
def pipes_to_shell(script: str) -> bool:
    """Whether ``script`` hands a ``curl`` or ``wget`` download to a shell or an interpreter to
    run: piped into one, or substituted into its command line (``$(curl ...)``, ``<(curl ...)``)."""
    commands = _commands(script)
    for i, command in enumerate(commands):
        if _fetched(command.words) == "" and _piped_into(commands, i) in _INTERPRETERS:
            return True
        substituted = any(_SUBSTITUTION.search(word) for word in command.words)
        if substituted and _head(command.words) in _INTERPRETERS:
            return True
    return False


@beartype
@require(lambda script, later: "\0" not in script + later, "a script holds no NUL")
@ensure(
    lambda result, script: not result or _FETCH_WORD.search(script) is not None,
    "a report needs a curl or wget in the step itself",
)
def unchecked_download(script: str, later: str = "") -> bool:
    """Whether ``script`` fetches a file with curl or wget that it, or the steps ``later`` in its
    job, goes on to run, install, unpack, chmod or put beside the commands before a sha256 check
    names it; or pipes a download into an unpacker."""
    mine = _commands(script)
    commands = mine + _commands(later)
    return any(_unchecked(commands, i) for i in range(len(mine)))


@beartype
@ensure(lambda result: result in ("error", "note"), "an error or a note")
@ensure(
    lambda result, severity: (result == "error") == (severity in ("Medium", "High")),
    "an error exactly for a High or Medium severity; any other word, a note",
)
def level_for(severity: str) -> str:
    """The SARIF level of a zizmor finding of this severity."""
    return "error" if severity in BLOCKING else "note"


class _Where(NamedTuple):
    path: str
    text: str
    job: str = ""  # the job being read, if any
    place: str = ""  # the job, or the job and its step, a finding's anchor starts with


def _line(where: _Where, needle: str) -> int:
    # The first line holding ``needle`` that is not a comment, after the job's own key when a job
    # is being read; YAML's loader keeps no line numbers.
    lines = where.text.splitlines()
    key = re.compile(rf"\s+['\"]?{re.escape(where.job)}['\"]?\s*:")
    start = next((i for i, t in enumerate(lines) if where.job and key.match(t)), 0)
    for n in range(start, len(lines)):
        if needle in lines[n] and not lines[n].lstrip().startswith("#"):
            return n + 1
    return start + 1


def _finding(where: _Where, key: str, needle: str, message: str) -> Finding:
    # ``needle`` is what the finding is about, as the file writes it: it finds the line and, after
    # the place, makes the anchor.
    rule, line = f"supply-chain/{key}", _line(where, needle)
    anchor = f"{where.place}: {needle}" if where.place else needle
    return Finding(rule, where.path, line, line, anchor, 0, 0, "error", message, RULES[rule].tags)


def _triggers(doc: dict[object, object]) -> set[str]:
    # PyYAML reads the key `on:` as the boolean True (YAML 1.1).
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    return {str(k) for k in on} if isinstance(on, (dict, list)) else set()


def _permissions(where: _Where, doc: dict[object, object]) -> list[Finding]:
    if "permissions" not in doc:
        why = "the workflow declares no top-level permissions"
        return [_finding(where, "token-permissions", "jobs:", why)]
    value = doc["permissions"]
    clean = value if isinstance(value, (str, dict)) else None
    if isinstance(clean, dict):
        clean = {str(k): str(v) for k, v in clean.items()}
    if grants_write(clean):
        why = "the top-level permissions grant a write; grant it on the job that needs it"
        return [_finding(where, "token-permissions", "permissions:", why)]
    return []


def _unpinned(where: _Where, uses: object) -> list[Finding]:
    if isinstance(uses, str) and not pinned(uses):
        why = f"{uses} is not pinned to a full commit"
        return [_finding(where, "pinned-action", uses, why)]
    return []


def _download(script: str) -> str:
    # The download command as the step writes it, its line: the finding's needle.
    for line in script.splitlines():
        if _FETCH_WORD.search(line) and not line.lstrip().startswith("#"):
            return line.strip()
    return script.split("\n", 1)[0].strip()


def _script(where: _Where, run: object, later: str = "") -> list[Finding]:
    # ``later``: the run scripts of the steps after this one in its job, where a use of what this
    # step fetched is a use.
    if not isinstance(run, str):
        return []
    found = [
        _finding(
            where,
            "script-injection",
            expression.strip(),
            f"the run script expands ${{{{{expression}}}}}, which an outsider controls; "
            "pass it through env",
        )
        for expression in untrusted(run)
    ]
    if pipes_to_shell(run):
        why = "a download is handed to a shell or an interpreter to run"
        found.append(_finding(where, "pipe-to-shell", _download(run), why))
    if unchecked_download(run, later):
        why = (
            "a download is run, installed, unpacked, chmodded or put beside the commands, in this "
            "step or a later one of the job, before a sha256 check names it; check it with "
            "sha256sum -c first"
        )
        found.append(_finding(where, "unchecked-download", _download(run), why))
    return found


def _checkout(where: _Where, step: dict[object, object]) -> list[Finding]:
    # Called for privileged workflows only: a checkout of the pull request's head or merge ref.
    uses, given = step.get("uses"), step.get("with")
    ref = str(given.get("ref", "")) if isinstance(given, dict) else ""
    if isinstance(uses, str) and "checkout" in uses and re.search(r"head|merge", ref):
        why = "a privileged workflow checks out the pull request's code"
        return [_finding(where, "untrusted-checkout", ref, why)]
    return []


def _steps(where: _Where, steps: list[object], dangerous: bool) -> list[Finding]:
    found = []
    shaped = [s for s in steps if isinstance(s, dict)]
    runs = [s.get("run") for s in shaped]
    for i, step in enumerate(shaped):
        label = step.get("name") or step.get("uses") or f"step {i + 1}"
        inside = where._replace(place=f"{where.job}/{label}")
        found += _unpinned(inside, step.get("uses"))
        later = "\n".join(r for r in runs[i + 1 :] if isinstance(r, str))
        found += _script(inside, step.get("run"), later)
        found += _checkout(inside, step) if dangerous else []
    return found


def _workflow(where: _Where) -> list[Finding]:
    try:
        doc = yaml.safe_load(where.text)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    dangerous = bool(_triggers(doc) & {"pull_request_target", "workflow_run"})
    found = _permissions(where, doc)
    jobs = doc.get("jobs")
    for job, body in jobs.items() if isinstance(jobs, dict) else []:
        if isinstance(body, dict):
            steps = body.get("steps")
            inside = where._replace(job=str(job), place=str(job))
            found += _unpinned(inside, body.get("uses"))
            found += _steps(inside, steps if isinstance(steps, list) else [], dangerous)
    return found


def _texts(tree: Path) -> dict[str, str]:
    # The workflow files, by their path relative to the tree, in a stable order; a link is never
    # read.
    home = tree / WORKFLOWS
    if not home.is_dir():
        return {}
    files = sorted(p for p in home.glob("*.y*ml") if p.is_file() and not p.is_symlink())
    return {p.relative_to(tree).as_posix(): p.read_text("utf-8", errors="replace") for p in files}


def _findings(tree: Path) -> tuple[Finding, ...]:
    return tuple(f for rel, text in _texts(tree).items() for f in _workflow(_Where(rel, text)))


class _Point(BaseModel):
    model_config = ConfigDict(extra="ignore")

    row: int  # from 0


class _Span(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_point: _Point
    end_point: _Point


class _Concrete(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: _Span


class _Symbolic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: dict[str, dict[str, object]]  # {"Local": {"verbatim_path": ...}} for a file
    annotation: str = ""
    kind: str = "Primary"  # Primary, Related or Hidden


class _Location(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbolic: _Symbolic
    concrete: _Concrete


class _Determinations(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: str
    confidence: str
    persona: str


class _Audit(BaseModel):
    # One finding of zizmor's JSON (its v1 format): the fields read.
    model_config = ConfigDict(extra="ignore")

    ident: str
    desc: str
    url: str = _ZIZMOR
    determinations: _Determinations
    locations: list[_Location]


def _zizmor(tree: Path, paths: list[str]) -> list[_Audit]:
    # zizmor over the workflow files, offline, at the regular persona, its ignore comments and
    # configuration files not honoured, and its exit code saying only whether it ran. A file it
    # cannot read as a workflow it passes over with a warning; when every file is such it says it
    # collected no input, which is no finding, not a failure.
    if not paths:
        return []
    argv = [_bin("zizmor"), "--offline", "--no-ignores", "--no-config", "--no-exit-codes"]
    argv += ["--no-progress", "--quiet", "--persona", "regular", "--format", "json", *paths]
    out = _tool(argv, cwd=tree)
    if out.returncode != 0:
        if "no inputs collected" in out.stderr:
            return []
        raise RuntimeError(f"zizmor failed: {(out.stderr or out.stdout).strip()}")
    raw = json.loads(out.stdout)
    return [_Audit.model_validate(item) for item in raw] if isinstance(raw, list) else []


def _audit_rule(audit: _Audit) -> Rule:
    name = "".join(w.capitalize() for w in audit.ident.split("-") if w)
    return Rule(
        f"zizmor/{audit.ident}",
        name or audit.ident,
        f"zizmor {audit.ident}: {audit.desc}.",
        "An audit of zizmor at its regular persona; a finding of High or Medium severity blocks "
        "when the change made it, a Low or Informational one is recorded.",
        audit.url,
        _TAGS,
    )


def _audited(audit: _Audit, texts: dict[str, str]) -> Finding | None:
    # The finding at the audit's primary location (the first location when none is marked so),
    # anchored to the text of its line; one in a file that was not given is passed over.
    place = next((loc for loc in audit.locations if loc.symbolic.kind == "Primary"), None)
    place = place or (audit.locations[0] if audit.locations else None)
    if place is None:
        return None
    local = place.symbolic.key.get("Local", {})
    rel = Path(str(local.get("verbatim_path", ""))).as_posix()
    if rel not in texts:
        return None
    span = place.concrete.location
    line, end = span.start_point.row + 1, max(span.start_point.row, span.end_point.row) + 1
    lines = texts[rel].splitlines()
    anchor = " ".join(lines[line - 1].split()) if line <= len(lines) else ""
    severity = audit.determinations.severity
    message = f"{audit.desc}: {place.symbolic.annotation} ({severity.lower()} severity)"
    rule = f"zizmor/{audit.ident}"
    return Finding(rule, rel, line, end, anchor, 0, 0, level_for(severity), message, _TAGS)


def _audits(tree: Path, rules: dict[str, Rule]) -> tuple[Finding, ...]:
    # zizmor's findings, and in ``rules`` the rule each of them names.
    texts = _texts(tree)
    found = []
    for audit in _zizmor(tree, sorted(texts)):
        finding = _audited(audit, texts)
        if finding is not None:
            rules[finding.rule] = _audit_rule(audit)
            found.append(finding)
    return tuple(found)


def _check(context: Context) -> Report:
    head, states = _compare(context, _findings)
    seen: dict[str, Rule] = {}
    audited, audit_states = _compare(context, lambda tree: _audits(tree, seen))
    rules = tuple(seen[r] for r in sorted({f.rule for f in audited}))
    version = _version([_bin("zizmor"), "--version"])
    reader = tuple(RULES.values())
    runs = (
        Run(GROUP.name, "beethoven-qa", __version__, _SCORECARD, reader, head, states),
        Run(GROUP.name, "zizmor", version, _ZIZMOR, rules, audited, audit_states),
    )
    stops = blocking(head + audited, states + audit_states)
    notes = sum(1 for f in audited if f.level == "note")
    summary = (
        f"{len(head)} workflow findings of the reader and {len(audited) - notes} of zizmor's of "
        f"medium or high severity in the tree, {len(stops)} new or worse; {notes} low ones recorded"
    )
    return Report(GROUP.name, runs, stops, not stops, summary)
