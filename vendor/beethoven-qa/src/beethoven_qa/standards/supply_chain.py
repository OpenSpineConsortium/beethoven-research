"""Supply chain: the GitHub Actions workflows that build, test and merge the code.

The Drive file's OpenSSF Scorecard section: a flawless codebase means little if the pipeline that
forges it can be subverted. Scorecard itself scores a whole repository against GitHub's API and
counts human review, which this project has chosen not to require (greg, 2026-10-04: the checks
alone decide a merge); so its checks that read the workflow files are applied here, to every file
under ``.github/workflows/``, the way Scorecard defines them:

- **Pinned-Dependencies**: every action a workflow uses, step or reusable workflow, is pinned to a
  full 40-character commit, never a tag or a branch that can be moved; a local action
  (``./path``) and a container pinned by its ``sha256`` digest are pinned. A download piped into a
  shell (``curl ... | sh``) runs whatever the server sends that day and is refused.
- **Token-Permissions**: every workflow declares top-level ``permissions`` that grant no write; a
  job that needs to write says so on the job.
- **Dangerous-Workflow**: a workflow started by ``pull_request_target`` or ``workflow_run`` runs
  with the repository's secrets and write token, so it never checks out the pull request's code;
  and no ``run`` script expands an expression an outsider controls (an issue's or a pull request's
  title or body, a branch name, a commit message), which would let that text run as shell.

Scorecard's other checks (branch protection, code review, signed releases, a security policy and
others) read GitHub's settings, not files; this repository's rulesets as code
(``.github/rulesets/main.json``) are its own record of the first.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import yaml
from beartype import beartype

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure
from beethoven_qa.standards.findings import Finding, blocking
from beethoven_qa.standards.group import Context, Group, Report, _compare
from beethoven_qa.standards.sarif import Rule, Run

__all__ = ["GROUP", "RULES", "grants_write", "pinned", "pipes_to_shell", "untrusted"]

GROUP = Group(
    "supply-chain",
    "the workflows that build, test and merge",
    "OpenSSF Scorecard's checks of workflow files",
)

WORKFLOWS = ".github/workflows"
_SCORECARD = "https://github.com/ossf/scorecard/blob/main/docs/checks.md"


def _rule(key: str, name: str, text: str, check: str) -> Rule:
    return Rule(
        f"supply-chain/{key}",
        name,
        text,
        f"OpenSSF Scorecard, {check}.",
        f"{_SCORECARD}#{check.lower()}",
        ("security", "supply-chain"),
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
            "No download is piped into a shell.",
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
_PIPE = re.compile(r"\b(curl|wget)\b[^|\n;&]*\|\s*(sudo\s+)?(-?\w+\s+)*(ba|da|z|k)?sh\b")
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
@ensure(lambda result, script: not result or "|" in script, "a pipe needs a |")
def pipes_to_shell(script: str) -> bool:
    """Whether ``script`` pipes a ``curl`` or ``wget`` download into a shell."""
    return _PIPE.search(script) is not None


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


def _script(where: _Where, run: object) -> list[Finding]:
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
        why = "a download is piped into a shell"
        found.append(_finding(where, "pipe-to-shell", "|", why))
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
    for i, step in enumerate(s for s in steps if isinstance(s, dict)):
        label = step.get("name") or step.get("uses") or f"step {i + 1}"
        inside = where._replace(place=f"{where.job}/{label}")
        found += _unpinned(inside, step.get("uses"))
        found += _script(inside, step.get("run"))
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


def _findings(tree: Path) -> tuple[Finding, ...]:
    home = tree / WORKFLOWS
    if not home.is_dir():
        return ()
    found: list[Finding] = []
    for path in sorted(p for p in home.glob("*.y*ml") if p.is_file() and not p.is_symlink()):
        text = path.read_text(encoding="utf-8", errors="replace")
        found += _workflow(_Where(path.relative_to(tree).as_posix(), text))
    return tuple(found)


def _check(context: Context) -> Report:
    head, states = _compare(context, _findings)
    rules = tuple(RULES.values())
    run = Run(GROUP.name, "beethoven-qa", __version__, _SCORECARD, rules, head, states)
    stops = blocking(head, states)
    summary = f"{len(head)} workflow findings in the tree, {len(stops)} new or worse"
    return Report(GROUP.name, (run,), stops, not stops, summary)
