"""The runner of ``python -m beethoven_qa standards <group>|all``: which commit the new code starts
from, that commit's tree beside the checkout, each group run on its own, its SARIF log written and
its verdict printed.

Which commit is the base, from ``--base``:

- a commit or ref, as given (CI gives a pull request's ``HEAD^1``, the base its merge commit was
  made on, and a push to ``main`` the commit it replaced);
- ``auto`` (the default, and the pre-push hook's): where the checkout left the remote's default
  branch, ``git merge-base HEAD origin/HEAD`` (``origin/main`` or ``origin/master`` when the remote
  names no default);
- ``none``: no base, every line new and every finding new; the whole tree held to every limit.

The new lines are those ``git diff`` gives between the base and the working tree, plus every line
of a file git does not track yet; the base's files are read from git's own objects (``git ls-tree``
and ``git cat-file``) into a temporary directory, never checked out over the working tree.

Exit 0 when every group asked for passed; 1 when one did not; 2 when one could not run (a tool
failed, git could not tell the base) or the repository's ``[tool.beethoven-qa]`` table is unusable.
Everything here reads, writes or runs git and the tools, so it is private and carries no contracts;
what it decides with is in the groups' contracted functions.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType

from beethoven_qa.gate import GateConfig
from beethoven_qa.standards import (
    coverage,
    design,
    duplication,
    readability,
    security,
    supply_chain,
)
from beethoven_qa.standards.findings import Finding
from beethoven_qa.standards.group import Context, Report
from beethoven_qa.standards.newcode import Changes, Ranges, changed_lines, renamed_files
from beethoven_qa.standards.sarif import sarif_log
from beethoven_qa.vendor import _git, _git_bytes, is_safe_relpath

__all__ = ["GROUPS"]

# Every group, in the order a person reads them; the name is the command line's and CI's job's.
GROUPS: dict[str, ModuleType] = {
    m.GROUP.name: m for m in (readability, design, duplication, coverage, security, supply_chain)
}


def _commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()


def _resolve(root: Path, base: str) -> str | None:
    # The base commit, or None for no base; ValueError when it cannot be told.
    if base == "none":
        return None
    if base != "auto":
        try:
            return _commit(root, base)
        except subprocess.CalledProcessError:
            raise ValueError(f"{base} is not a commit of this repository") from None
    candidates = ["refs/remotes/origin/main", "refs/remotes/origin/master"]
    try:
        named = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
        candidates.insert(0, named)
    except subprocess.CalledProcessError:
        pass
    for ref in candidates:
        try:
            return _git(root, "merge-base", "HEAD", _commit(root, ref)).strip()
        except subprocess.CalledProcessError:
            continue
    raise ValueError("the remote's default branch is not known here; pass --base <commit>")


def _untracked(root: Path) -> list[tuple[str, Ranges]]:
    # Files git does not track yet: every line of each is new.
    names = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\x00")
    found: list[tuple[str, Ranges]] = []
    for rel in sorted(n for n in names if n):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            if count:
                found.append((rel, ((1, count),)))
    return found


def _changes(root: Path, base: str | None) -> Changes:
    if base is None:
        return Changes((), (), True)
    quiet = ("-c", "core.quotePath=false")
    # The prefixes are given, since a person's diff.noPrefix or diff.mnemonicPrefix would change
    # the "+++ b/" header the new lines are read by.
    plain = ("--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "-M")
    diff = _git(root, *quiet, "diff", "--unified=0", *plain, base)
    names = _git(root, *quiet, "diff", "--name-status", "--no-ext-diff", "-M", "-z", base)
    lines = dict(changed_lines(diff))
    lines.update(_untracked(root))
    return Changes(tuple(sorted(lines.items())), renamed_files(names), False)


def _extract(root: Path, base: str, paths: list[str], into: Path) -> None:
    # The base commit's files under ``paths``, read from git's objects the way the pinned copy is
    # (vendor._blobs): ``git archive`` would apply the attributes export-ignore and export-subst,
    # and a file it left out would make every finding in it look new. A link, a submodule and a
    # name that is not UTF-8 are passed over; no file of the working tree is read.
    listing = _git_bytes(root, "ls-tree", "-r", "-z", "--full-tree", base, "--", *paths)
    for item in listing.split(b"\x00"):
        meta, _, raw = item.partition(b"\t")
        mode, kind, oid = (meta.decode("ascii").split(" ") + ["", "", ""])[:3]
        try:
            rel = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if kind == "blob" and mode in ("100644", "100755") and is_safe_relpath(rel):
            target = into / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git_bytes(root, "cat-file", "blob", oid))


def _line(finding: Finding, state: str) -> str:
    why = {"new": "new", "updated": "worse than before"}.get(state, state)
    return f"  {finding.path}:{finding.line}: {finding.rule}: {finding.message} ({why})"


def _print(report: Report) -> None:
    verdict = "passed" if report.passed else "FAILED"
    head, *rest = report.summary.splitlines() or [""]
    print(f"standards: {report.group}: {verdict}: {head}")
    for extra in rest:
        print(f"  {extra}")
    states = {f: s for run in report.runs for f, s in zip(run.findings, run.states, strict=True)}
    for finding in report.blocking:
        print(_line(finding, states.get(finding, "new")))


def _write(report: Report, sarif: Path | None) -> None:
    if sarif is None:
        return
    sarif.mkdir(parents=True, exist_ok=True)
    text = json.dumps(sarif_log(report.runs), indent=2, ensure_ascii=False)
    (sarif / f"{report.group}.sarif").write_text(text + "\n", encoding="utf-8")


def _one(name: str, context: Context, sarif: Path | None) -> int:
    try:
        report: Report = GROUPS[name]._check(context)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"standards: {name}: could not run: {exc}")
        return 2
    _write(report, sarif)
    _print(report)
    return 0 if report.passed else 1


def _run(root: Path, names: list[str], base: str = "auto", sarif: Path | None = None) -> int:
    root = root.resolve()
    try:
        config = GateConfig.from_pyproject(root)
        commit = _resolve(root, base)
        changes = _changes(root, commit)
    except (OSError, ValueError) as exc:
        print(f"standards: {exc}")
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"standards: git failed: {exc}")
        return 2
    package = f"{config.src}/{config.package}".strip("/")
    where = f"against {commit[:12]}" if commit else "on the whole tree (no base)"
    print(f"standards: {', '.join(names)} on {package} and {supply_chain.WORKFLOWS}, {where}")
    with tempfile.TemporaryDirectory(prefix="beethoven-qa-base-") as tmp:
        tree = Path(tmp) if commit else None
        if commit:
            _extract(root, commit, [package, supply_chain.WORKFLOWS], Path(tmp))
        context = Context(root, tree, package, changes)
        return max(_one(name, context, sarif) for name in names)
