"""The interface standard's five rules, checked on one repository: ``python -m beethoven_qa
boundary [--root DIR]``.

The standard (INTERFACES.md in the project's hub, linked from interface/README.md) makes every
repository of the Beethoven ecosystem a sealed unit that another reaches only through what it
publishes, at a version, through a copy pinned to a merged commit. This command checks its rules on
the repository it is pointed at, the same code for beethoven-qa itself and for every consumer, which
runs it from its pinned copy:

1. **A sealed tree.** No tracked file but prose (``.md``) reaches another repository's checkout: a
   path built from the checkout's parent, a variable named for another checkout, a clone or a git
   URL of another repository of the organisation, a submodule, a link into one, a dependency by
   git URL or by a path outside the tree in ``pyproject.toml`` or ``pixi.toml``, or an
   ``actions/checkout`` step that names a repository.
2. **One manifest.** ``interface/INTERFACE.json`` has the standard's shape; every export path names
   a file; ``interface/`` holds nothing no export lists but the manifest and its README; and every
   copy the repository carries (a tracked ``ORIGIN``) is one of its imports.
3. **Pinned copies.** Each import's folder holds exactly the files its ORIGIN records, byte for
   byte, beside ORIGIN, README.md and pull.sh; ORIGIN names the import's repository and a commit,
   and its export line, when it has one, the import's export and version. beethoven-qa's own copy
   keeps its own record and is ``vendor-check``'s to check; here it is recognised, and the copy's
   manifest is asked whether it offers what the import names.
4. **The published surface.** A Python import (read from the syntax tree, never run) of another
   engine package names only the modules and names that package's ``python`` export lists, read
   from the provider's manifest in this repository's pinned copy of it (``<folder>/interface/``,
   the provider's own layout, which beethoven-qa's copy keeps); a package with no such copy fails.
5. **The direction.** beethoven-qa <- beethoven-agents <- beethoven-research, never back, in the
   manifest's imports and in Python's; beethoven-qa imports nothing of ours.

The pinned copies hold the provider's files, checked by the provider and by rule 3, so rules 1 and
4 do not read them. A finding of rule 1 or 4 a repository cannot remove yet is named under
``[tool.beethoven-qa.boundary]`` in its pyproject.toml, file, then what matched, then the reason
(``gate.GateConfig`` validates the table). Every entry is printed on every run with its reason, and
one that matches nothing fails, so the list only shrinks; a new entry is a person's decision in a
reviewed change, the way an ``unproven`` entry is. Rules 2, 3 and 5 take no exception.

The functions that carry contracts are pure and decide; the private ones read the tree, ask git and
print, carry none, and are called by no contracted function, so CrossHair never runs them.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple, get_args

import yaml
from beartype import beartype
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from beethoven_qa.contracts import ensure, require
from beethoven_qa.gate import GateConfig
from beethoven_qa.vendor import COPY_ROOTS, KIT, VENDOR_DIR, _git, _toplevel, is_safe_relpath

__all__ = [
    "CHECKOUT_VARIABLES",
    "ENGINE",
    "KINDS",
    "MANIFEST",
    "OWNER",
    "REPOSITORIES",
    "STANDARD",
    "Breach",
    "Difference",
    "Export",
    "Import",
    "Manifest",
    "Recorded",
    "against_direction",
    "compare_copy",
    "covered",
    "line_reaches",
    "read_origin",
    "stale_exceptions",
    "unpublished",
]

OWNER = "OpenSpineConsortium"
STANDARD = f"https://github.com/{OWNER}/beethoven/blob/main/INTERFACES.md"
MANIFEST = "interface/INTERFACE.json"
# What interface/ may hold that no export lists, and what an import's folder may hold beside the
# export's files.
_OWN_FILES = (MANIFEST, "interface/README.md")
_FOLDER_FILES = ("ORIGIN", "README.md", "pull.sh")

# Every repository of the ecosystem, the names two of the launcher's checkouts had before (the
# folders ashley and ashley-bridge), the website's, and the studies'.
REPOSITORIES: tuple[str, ...] = (
    "beethoven",
    "beethoven-agents",
    "beethoven-base",
    "beethoven-desktop",
    "beethoven-extension",
    "beethoven-qa",
    "beethoven-research",
    "beethoven-sites",
    "beethoven-vscode",
    "hpcworker",
    "ashley",
    "ashley-bridge",
    "openspineconsortium.github.io",
    "spinesurg-ct-nnunet",
    "u251-xenograft-rat-rnaseq-study",
)
# The engine's repositories and their packages, in the one direction of dependence: each may
# import only the ones before it.
ENGINE: tuple[tuple[str, str], ...] = (
    ("beethoven-qa", "beethoven_qa"),
    ("beethoven-agents", "beethoven_agents"),
    ("beethoven-research", "beethoven_research"),
)
_ORDER = tuple(name for name, _ in ENGINE)
_PROVIDER = {package: name for name, package in ENGINE}
_QA = f"{OWNER}/{_ORDER[0]}"

# Variables whose value is another repository's checkout (hpcworker's test_boundary.py names the
# same). Spelled from their parts, so this file does not match its own rule.
_STEMS = ("HPCWORKER", "ASHLEY", "SITES", "QA") + tuple(
    f"BEETHOVEN_{stem}"
    for stem in (
        "SERVICE",
        "QA",
        "AGENTS",
        "RESEARCH",
        "SITES",
        "EXTENSION",
        "DESKTOP",
        "VSCODE",
        "BASE",
        "HUB",
    )
)
CHECKOUT_VARIABLES: tuple[str, ...] = tuple(f"{stem}_DIR" for stem in _STEMS)

_NAMES = "|".join(re.escape(n) for n in sorted(REPOSITORIES, key=len, reverse=True))
# The hub's name is also the product's, in addresses and folders (the site's "/beethoven/" pages,
# the lab's "shared/beethoven"), so only the parent directory followed by it counts as its checkout.
_COMPUTED_NAMES = "|".join(
    re.escape(n) for n in sorted(REPOSITORIES, key=len, reverse=True) if n != "beethoven"
)
# A path made from the checkout's parent: the parent directory itself, or a command's or a
# variable's output followed by a repository's name ("$(dirname "$HERE")" and the like).
_SIBLING = re.compile(r"\.\./(" + _NAMES + r")(?![A-Za-z0-9_.-])")
_COMPUTED = re.compile(r"[)}]/(" + _COMPUTED_NAMES + r")(?![A-Za-z0-9_.-])")
_VARIABLE = re.compile(r"\b(" + "|".join(CHECKOUT_VARIABLES) + r")\b")


def _anycase(text: str) -> str:
    # ``text`` as a pattern whatever the case of its ASCII letters. Spelled out rather than given
    # re.IGNORECASE: CrossHair 0.0.111 compiles each literal of an IGNORECASE pattern unescaped, so
    # the "+" of "git+https" stopped it with "nothing to repeat" and the "." of a name matched any
    # character (crosshair/libimpl/relib.py, unicode_ignorecase_mask; seen on CI, 2026-10-07).
    return "".join(
        f"[{c.lower()}{c.upper()}]" if c.isascii() and c.isalpha() else re.escape(c) for c in text
    )


# An address of another repository of the organisation, in any case, as GitHub reads it.
_AT = (
    _anycase(OWNER)
    + "/("
    + "|".join(_anycase(n) for n in sorted(REPOSITORIES, key=len, reverse=True))
    + r")(?![A-Za-z0-9_-])"
)
_COMMANDS = (
    r"\s+".join(_anycase(w) for w in c.split())
    for c in ("git clone", "gh repo clone", "git submodule add")
)
_CLONE = re.compile(r"\b(?:" + "|".join(_COMMANDS) + r")\b[^\n]*?" + _AT)
_GIT_URL = re.compile(
    _anycase("git") + r"\+(?:" + _anycase("http") + "[sS]?|" + _anycase("ssh") + r")://\S*?" + _AT
)
_LINE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_SIBLING, "a path built from this checkout's parent names another repository's checkout"),
    (_COMPUTED, "a path built from this checkout's parent names another repository's checkout"),
    (_VARIABLE, "a variable named for another repository's checkout"),
    (_CLONE, "a clone of another repository of the organisation"),
    (_GIT_URL, "a git URL of another repository of the organisation"),
)
_WHY = frozenset(why for _, why in _LINE_RULES)

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-z][a-z0-9-]*")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")


@beartype
@ensure(lambda result, line: all(what in line for what, _ in result), "each match is in the line")
@ensure(
    lambda result, own: all(what.lower() != own.lower() for what, _ in result),
    "the repository's own name is never a reach",
)
@ensure(lambda result: all(why in _WHY for _, why in result), "each says which kind of reach")
@ensure(lambda result, line: line.strip() != "" or result == (), "a blank line reaches nothing")
def line_reaches(line: str, own: str) -> tuple[tuple[str, str], ...]:
    """Every way ``line`` reaches another repository's checkout (rule 1), as (what matched, why):
    a repository's name or a variable's, with the kind of reach. ``own`` is the repository's own
    name, which a line may name."""
    return tuple(
        (match.group(1), why)
        for pattern, why in _LINE_RULES
        for match in pattern.finditer(line)
        if match.group(1).lower() != own.lower()
    )


def _part(pattern: str, name: str) -> bool:
    # `*` matches any run of characters inside one part of a path; the rest matches itself.
    first, *rest = pattern.split("*")
    if not rest:
        return pattern == name
    if not name.startswith(first):
        return False
    position = len(first)
    *middle, last = rest
    for piece in middle:
        found = name.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return len(name) - position >= len(last) and name.endswith(last)


def _covers(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    parts, names = pattern.split("/"), path.split("/")
    return len(parts) == len(names) and all(_part(p, n) for p, n in zip(parts, names, strict=True))


# The precondition is the real domain (a path git tracks, a file under interface/, an export's
# path, which Export refuses with a NUL), and it keeps CrossHair 0.0.111 away from its fault with
# symbolic strings that hold NUL: on CI (2026-10-07) it reported the second postcondition false
# for covered("\\x00/\\x00\\x00", ("\\x00/",)), which returns True and holds it when run.
@beartype
@require(
    lambda path, patterns: "\x00" not in path and all("\x00" not in p for p in patterns),
    "paths of a tree and of a manifest, which never hold NUL",
)
@ensure(lambda result, path, patterns: path not in patterns or result, "a listed path is covered")
@ensure(
    lambda result, path, patterns: (
        not result
        or any(
            p == path
            or (p.endswith("/") and path.startswith(p))
            or ("*" in p and p.count("/") == path.count("/"))
            for p in patterns
        )
    ),
    "covered only by itself, a folder above it, or a pattern of as many parts",
)
def covered(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether one of an export's ``patterns`` covers the file ``path`` (rule 2): a pattern ending
    in ``/`` covers every file under it, and ``*`` matches within one part of a path."""
    return any(_covers(p, path) for p in patterns)


class Recorded(NamedTuple):
    """What an ORIGIN record of the standard says (rule 3): the repository and the commit of its
    first two lines ("" when either is missing or malformed), its export lines as (export,
    version), its sha256 lines as (path, sha256), and the export and sha256 lines it could not
    read. Lines of any other key are left alone."""

    repository: str
    commit: str
    exports: tuple[tuple[str, str], ...]
    files: tuple[tuple[str, str], ...]
    unread: tuple[str, ...]


def _head(lines: list[str], index: int, key: str) -> str:
    line = lines[index] if index < len(lines) else ""
    return line.removeprefix(f"{key} ") if line.startswith(f"{key} ") else ""


def _entry(line: str, seen: set[str]) -> tuple[str, str, str]:
    # (kind, first, second) of an export or sha256 line, kind "" when it is not in the record's
    # form, "-" when it is some other key.
    key, _, rest = line.partition(" ")
    first, _, second = rest.partition(" ")
    if key == "export":
        good = bool(first) and second.isdigit()
    elif key == "sha256":
        good = _HEX64.fullmatch(first) is not None and is_safe_relpath(second)
        good = good and second not in seen
        seen.add(second)
    else:
        return "-", "", ""
    return (key if good else ""), first, second


@beartype
@ensure(lambda result: result.commit == "" or _HEX40.fullmatch(result.commit) is not None)
@ensure(
    lambda result: all(_HEX64.fullmatch(digest) is not None for _, digest in result.files),
    "every file's digest is a sha256 in lower-case hex",
)
@ensure(
    lambda result: all(is_safe_relpath(path) for path, _ in result.files),
    "every file is a path inside the folder",
)
@ensure(lambda result: len({p for p, _ in result.files}) == len(result.files), "each file once")
@ensure(
    lambda result: all(v.isdigit() for _, v in result.exports), "an export's version is a number"
)
def read_origin(text: str) -> Recorded:
    """Read an ORIGIN record of the standard: ``repository <owner/name>``, ``commit <40 hex>``, any
    ``export <id> <version>`` lines, and one ``sha256 <64 hex> <path>`` line per file."""
    lines = [line for line in text.split("\n") if line]
    commit = _head(lines, 1, "commit")
    exports: list[tuple[str, str]] = []
    files: list[tuple[str, str]] = []
    unread: list[str] = []
    seen: set[str] = set()
    for line in lines[2:]:
        kind, first, second = _entry(line, seen)
        if kind == "export":
            exports.append((first, second))
        elif kind == "sha256":
            files.append((second, first))
        elif kind == "":
            unread.append(line)
    commit = commit if _HEX40.fullmatch(commit) else ""
    return Recorded(
        _head(lines, 0, "repository"), commit, tuple(exports), tuple(files), tuple(unread)
    )


class Difference(NamedTuple):
    """How a copy's folder differs from its record: files recorded and absent, present and not
    recorded, and present with other bytes; each sorted."""

    missing: tuple[str, ...]
    extra: tuple[str, ...]
    changed: tuple[str, ...]


@beartype
@require(lambda recorded: len({p for p, _ in recorded}) == len(recorded), "each path once")
@require(lambda present: len({p for p, _ in present}) == len(present), "each path once")
@ensure(
    lambda result, recorded, present: (
        set(result.missing) == {p for p, _ in recorded} - {p for p, _ in present}
    ),
    "missing: recorded and not present",
)
@ensure(
    lambda result, recorded, present: (
        set(result.extra) == {p for p, _ in present} - {p for p, _ in recorded}
    ),
    "extra: present and not recorded",
)
@ensure(
    lambda result, recorded, present: (
        set(result.changed)
        == {p for p, d in present if p in dict(recorded) and dict(recorded)[p] != d}
    ),
    "changed: in both, with another digest",
)
@ensure(lambda result: all(list(part) == sorted(part) for part in result), "each in a stable order")
def compare_copy(
    recorded: tuple[tuple[str, str], ...], present: tuple[tuple[str, str], ...]
) -> Difference:
    """Compare a copy's files as its ORIGIN records them with the files its folder holds, both as
    (path, sha256) pairs (rule 3)."""
    want, have = dict(recorded), dict(present)
    return Difference(
        tuple(sorted(want.keys() - have.keys())),
        tuple(sorted(have.keys() - want.keys())),
        tuple(sorted(p for p in want.keys() & have.keys() if want[p] != have[p])),
    )


@beartype
@ensure(lambda result: list(result) == sorted(set(result)), "each once, in a stable order")
@ensure(
    lambda result, module, names, surface: (
        len(names) > 0 or result == (() if module in surface else (module,))
    ),
    "a plain import names a listed module, or it is reported",
)
@ensure(
    lambda result, module, names, surface: (
        len(names) == 0
        or set(result)
        == {
            f"{module}.{n}"
            for n in names
            if f"{module}.{n}" not in surface and n not in surface.get(module, ())
        }
    ),
    "a name is published when the module lists it or it is a listed module itself",
)
def unpublished(
    module: str, names: tuple[str, ...], surface: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """What an import takes that a provider's python export does not list (rule 4): ``import
    module`` when ``names`` is empty, else ``from module import names``; ``surface`` is the
    export's module -> names. A star is never listed, so it is always reported."""
    if not names:
        return () if module in surface else (module,)
    return tuple(
        sorted(
            {
                f"{module}.{n}"
                for n in names
                if f"{module}.{n}" not in surface and n not in surface.get(module, ())
            }
        )
    )


def _against(own: str, other: str) -> bool:
    if other == own:
        return False
    if own == _ORDER[0]:
        return True
    return other in _ORDER and _ORDER.index(other) > _ORDER.index(own)


@beartype
@ensure(lambda result, imported: set(result) <= set(imported), "only what was imported")
@ensure(lambda result: len(set(result)) == len(result), "each once")
@ensure(
    lambda result, own, imported: own != _ORDER[0] or set(result) == set(imported) - {own},
    "beethoven-qa depends on nothing of ours",
)
@ensure(
    lambda result, own, imported: (
        own not in _ORDER
        or set(result)
        >= {x for x in imported if x in _ORDER and _ORDER.index(x) > _ORDER.index(own)}
    ),
    "no engine repository imports one after it",
)
@ensure(lambda result, own: own in _ORDER or result == (), "the direction binds the engine")
def against_direction(own: str, imported: tuple[str, ...]) -> tuple[str, ...]:
    """The repositories ``own`` imports against the engine's direction (rule 5), in the order
    given: beethoven-qa imports nothing of ours, and no engine repository imports one that comes
    after it in qa <- agents <- research."""
    if own not in _ORDER:
        return ()
    return tuple(dict.fromkeys(x for x in imported if _against(own, x)))


@beartype
@ensure(lambda result: list(result) == sorted(result), "listed in a stable order")
@ensure(lambda result, found, known: set(result) == known - found, "exactly the unmatched entries")
def stale_exceptions(
    found: frozenset[tuple[str, str]], known: frozenset[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The known exceptions, as (file, what matched), that no finding matches any more: an entry
    that excuses nothing must go."""
    return sorted(known - found)


# --- the manifest: the boundary where interface/INTERFACE.json is read --------------------------

_Kind = Literal["contract", "schema", "data", "python", "cli", "files", "program", "text"]
KINDS: tuple[str, ...] = get_args(_Kind)


class Export(BaseModel):
    """One export of a manifest, in the standard's shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: _Kind
    version: int = Field(ge=1, strict=True)
    summary: str
    paths: tuple[str, ...] = Field(min_length=1)
    consumers: tuple[str, ...]

    @model_validator(mode="after")
    def _shape(self) -> Export:
        if _ID.fullmatch(self.id) is None:
            raise ValueError(f"export id {self.id!r} is not a lower-case name")
        for path in self.paths:
            if not is_safe_relpath(path.removesuffix("/")):
                raise ValueError(f"export path {path!r} is not a path inside the repository")
        for consumer in self.consumers:
            if _REPOSITORY.fullmatch(consumer) is None:
                raise ValueError(f"consumer {consumer!r} is not an owner/name of a repository")
        return self


class Import(BaseModel):
    """One import of a manifest, in the standard's shape: ORIGIN lives in the import's folder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    export: str
    version: int = Field(ge=1, strict=True)
    into: str
    origin: str

    @model_validator(mode="after")
    def _shape(self) -> Import:
        if _REPOSITORY.fullmatch(self.repository) is None:
            raise ValueError(f"repository {self.repository!r} is not an owner/name")
        if _ID.fullmatch(self.export) is None:
            raise ValueError(f"export {self.export!r} is not a lower-case name")
        if not is_safe_relpath(self.into) or self.origin != f"{self.into}/ORIGIN":
            raise ValueError(f"into {self.into!r} is no folder with its ORIGIN at {self.origin!r}")
        return self


class Manifest(BaseModel):
    """``interface/INTERFACE.json``, format 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1]
    name: str
    repository: str
    summary: str
    standard: str
    exports: tuple[Export, ...]
    imports: tuple[Import, ...]

    @model_validator(mode="after")
    def _shape(self) -> Manifest:
        if self.repository != f"{OWNER}/{self.name}":
            raise ValueError(f"repository {self.repository!r} is not {OWNER}/{self.name}")
        if self.standard != STANDARD:
            raise ValueError(f"standard {self.standard!r} is not {STANDARD}")
        ids = [e.id for e in self.exports]
        if len(set(ids)) != len(ids):
            raise ValueError(f"an export id is used twice: {', '.join(sorted(ids))}")
        return self


_SURFACE = TypeAdapter(dict[str, tuple[str, ...]])
# Where a python export keeps its module -> names file.
_SURFACE_FILE = ("interface/*.json",)


# --- I/O: private, uncontracted, never a gate target, called by no contracted function ------------


class Breach(NamedTuple):
    """One finding: where (line 0 for the whole file), which rule, what matched (the name a known
    exception gives it), and what is wrong, in plain words."""

    path: str
    line: int
    rule: int
    what: str
    message: str


class _CannotRun(Exception):
    """A reason the rules cannot be checked here at all."""


class _Repo(NamedTuple):
    # The repository as the rules read it: what git tracks (and which of it are submodules), its
    # manifest and name when they can be read, the folders that hold other repositories' files,
    # and the known exceptions.
    root: Path
    tracked: tuple[str, ...]
    gitlinks: tuple[str, ...]
    manifest: Manifest | None
    copies: tuple[str, ...]
    known: dict[str, dict[str, str]]


_ADVICE = {
    1: "rule 1, a sealed tree: nothing tracked reads, runs or copies another repository's "
    "checkout; take what is needed from that repository's interface, as a pinned copy",
    2: "rule 2, one manifest: interface/INTERFACE.json declares every export and every import, "
    "in the standard's shape",
    3: "rule 3, pinned copies: an import's folder holds exactly what its ORIGIN records; take a "
    "new copy with its pull.sh, never edit one by hand",
    4: "rule 4, the published surface: import only the modules and names the provider's python "
    "export lists",
    5: "rule 5, the direction: beethoven-qa <- beethoven-agents <- beethoven-research, never "
    "back, and beethoven-qa imports nothing of ours",
}


def _listing(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # Every path git tracks, and the ones that are a submodule's commit (a gitlink, mode 160000).
    tracked: dict[str, None] = {}
    links: list[str] = []
    for item in _git(root, "ls-files", "-s", "-z").split("\x00"):
        meta, _, path = item.partition("\t")
        if path:
            tracked[path] = None
            if meta.startswith("160000 "):
                links.append(path)
    return tuple(tracked), tuple(links)


def _known(root: Path) -> dict[str, dict[str, str]]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            has_table = "beethoven-qa" in tomllib.load(fh).get("tool", {})
        return GateConfig.from_pyproject(root).boundary if has_table else {}
    except (OSError, ValueError) as exc:  # unreadable, bad TOML, or the table's own refusal
        raise _CannotRun(f"the [tool.beethoven-qa] table cannot be used: {exc}") from None


def _manifest(root: Path) -> tuple[Manifest | None, list[Breach]]:
    path = root / MANIFEST
    if path.is_symlink() or not path.is_file():
        return None, [
            Breach(
                MANIFEST,
                0,
                2,
                MANIFEST,
                "is missing: every repository declares in it what "
                "it exports and imports, even when that is nothing",
            )
        ]
    try:
        return Manifest.model_validate_json(path.read_bytes()), []
    except ValidationError as exc:
        return None, [
            Breach(
                MANIFEST,
                0,
                2,
                MANIFEST,
                f"{'.'.join(map(str, e['loc'])) or 'the file'}: {e['msg']}",
            )
            for e in exc.errors()
        ]


def _load(root: Path) -> tuple[_Repo, list[Breach]]:
    if _toplevel(root) != root:
        raise _CannotRun(f"{root} is not the root of a git checkout")
    known = _known(root)
    tracked, links = _listing(root)
    manifest, found = _manifest(root)
    folders = [VENDOR_DIR, *(i.into for i in (manifest.imports if manifest else ()))]
    copies = tuple(f"{folder}/" for folder in dict.fromkeys(folders))
    return _Repo(root, tracked, links, manifest, copies, known), found


def _own(repo: _Repo) -> str:
    return repo.manifest.name if repo.manifest else ""


def _text(path: Path) -> str | None:
    # A link is read as where it points (a link into a sibling checkout is a reach), a file as
    # UTF-8 text; anything else (binary, unreadable) has no lines to reach with.
    try:
        if path.is_symlink():
            return os.readlink(path)
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _line_of(text: str, needle: str) -> int:
    return next((n for n, line in enumerate(text.splitlines(), 1) if needle in line), 0)


# --- rule 1 --------------------------------------------------------------------------------------

_BY_URL = re.compile(r"(?:^|@\s*)(?:git\+|file:)")
# Tables of dependencies whose name does not end in "dependencies" (uv's, and PEP 735's).
_DEPENDENCY_TABLES = ("sources", "dependency-groups")


def _leaves(path: str) -> bool:
    pure = PurePosixPath(path.replace("\\", "/"))
    return pure.is_absolute() or ".." in pure.parts or path.startswith("~") or ":" in path


def _reaching(value: object, inside: bool) -> Iterator[str]:
    # The dependency specifications under a dependency table that reach outside the tree: a git
    # URL, a path that leaves it, or a requirement string "name @ git+..." or "@ file:...".
    if isinstance(value, dict):
        yield from _reaching_table(value, inside)
    elif isinstance(value, list):
        for item in value:
            yield from _reaching(item, inside)
    elif inside and isinstance(value, str) and _BY_URL.search(value):
        yield value


def _reaching_table(table: dict[object, object], inside: bool) -> Iterator[str]:
    # Under a dependency table, a table that names git or a path is one specification (pixi's and
    # uv's form); elsewhere it is a table to look into.
    spec = table.get("git") if inside else None
    path = table.get("path") if inside else None
    if isinstance(spec, str) or (isinstance(path, str) and _leaves(path)):
        yield str(spec if isinstance(spec, str) else path)
        return
    for key, item in table.items():
        nested = str(key).endswith("dependencies") or str(key) in _DEPENDENCY_TABLES
        yield from _reaching(item, inside or nested)


def _dependencies(rel: str, text: str) -> list[Breach]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [
            Breach(
                rel,
                0,
                1,
                rel,
                f"cannot be read as TOML ({exc}), so its dependencies cannot be checked",
            )
        ]
    return [
        Breach(
            rel,
            _line_of(text, spec),
            1,
            spec,
            f"a dependency by git URL or by a path outside the tree: {spec}",
        )
        for spec in _reaching(data, False)
    ]


def _steps(data: object) -> list[dict[object, object]]:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    found: list[dict[object, object]] = []
    for job in jobs.values() if isinstance(jobs, dict) else ():
        steps = job.get("steps") if isinstance(job, dict) else None
        found += [s for s in steps or () if isinstance(s, dict)]
    return found


def _checked_out(step: dict[object, object]) -> str | None:
    used, given = step.get("uses"), step.get("with")
    if isinstance(used, str) and used.startswith("actions/checkout") and isinstance(given, dict):
        named = given.get("repository")
        return None if named is None else str(named)
    return None


def _checkouts(rel: str, text: str) -> list[Breach]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [
            Breach(
                rel,
                0,
                1,
                rel,
                f"cannot be read as YAML ({exc}), so its checkouts cannot be checked",
            )
        ]
    named = [name for step in _steps(data) if (name := _checked_out(step)) is not None]
    return [
        Breach(
            rel,
            _line_of(text, name),
            1,
            name,
            f"an actions/checkout step checks out another repository: {name}",
        )
        for name in named
    ]


def _reaches(repo: _Repo, rel: str) -> list[Breach]:
    text = _text(repo.root / rel)
    if text is None:
        return []
    found = [
        Breach(rel, n, 1, what, f"{why}: {what}")
        for n, line in enumerate(text.splitlines(), 1)
        for what, why in line_reaches(line, _own(repo))
    ]
    if rel.rsplit("/", 1)[-1] in ("pyproject.toml", "pixi.toml"):
        found += _dependencies(rel, text)
    if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
        found += _checkouts(rel, text)
    return found


def _ours(repo: _Repo) -> list[str]:
    # The tracked files that are this repository's own code: not prose, not a pinned copy.
    return [
        rel
        for rel in repo.tracked
        if not rel.endswith(".md") and not rel.startswith(repo.copies) and rel not in repo.gitlinks
    ]


def _sealed(repo: _Repo) -> list[Breach]:
    found = [breach for rel in _ours(repo) for breach in _reaches(repo, rel)]
    found += [
        Breach(rel, 0, 1, rel, "a submodule: another repository's commit inside this tree")
        for rel in repo.gitlinks
    ]
    if ".gitmodules" in repo.tracked:
        found.append(Breach(".gitmodules", 0, 1, ".gitmodules", "declares submodules"))
    return found


# --- rule 2 --------------------------------------------------------------------------------------


def _names_a_file(root: Path, pattern: str) -> bool:
    if pattern.endswith("/"):
        folder = root / pattern
        return folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))
    if "*" in pattern:
        return any(p.is_file() for p in root.glob(pattern))
    return (root / pattern).is_file()


def _files_under(base: Path) -> list[str]:
    # Every file under ``base``, relative to it; Python's byte-code caches are not files of ours.
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        found += [(Path(dirpath) / n).relative_to(base).as_posix() for n in filenames]
    return sorted(found)


def _declared(repo: _Repo, manifest: Manifest) -> list[Breach]:
    found = [
        Breach(MANIFEST, 0, 2, path, f"export {e.id} lists {path}, which names no file")
        for e in manifest.exports
        for path in e.paths
        if not _names_a_file(repo.root, path)
    ]
    patterns = tuple(path for e in manifest.exports for path in e.paths)
    found += [
        Breach(rel, 0, 2, rel, "is in interface/ but no export lists it: list it, or move it")
        for rel in (f"interface/{r}" for r in _files_under(repo.root / "interface"))
        if rel not in _OWN_FILES and not covered(rel, patterns)
    ]
    origins = {i.origin for i in manifest.imports}
    found += [
        Breach(
            rel,
            0,
            2,
            rel,
            "records a copy interface/INTERFACE.json does not import: declare it under imports",
        )
        for rel in repo.tracked
        if rel.rsplit("/", 1)[-1] == "ORIGIN" and rel not in origins
    ]
    return found


# --- rule 3 --------------------------------------------------------------------------------------


def _provider_manifest(folder: Path) -> tuple[Manifest | None, str]:
    path = folder / MANIFEST
    if not path.is_file():
        return None, f"{path} is missing"
    try:
        return Manifest.model_validate_json(path.read_bytes()), ""
    except ValidationError as exc:
        return None, f"{path} cannot be read: {exc.errors()[0]['msg']}"


def _qa_copy(repo: _Repo, imp: Import) -> list[Breach]:
    said = _qa_refusal(repo.root, imp)
    return [Breach(MANIFEST, 0, 3, imp.export, said)] if said else []


def _qa_refusal(root: Path, imp: Import) -> str:
    # Why an import of beethoven-qa does not hold, or "" when it does: the copy is the vendor
    # command's, in its one folder, and its own manifest offers the export at that version with
    # files the copy carries (the docs are read at the release, never imported).
    if imp.into != VENDOR_DIR:
        return (
            f"beethoven-qa is taken only with its vendor command, into {VENDOR_DIR}, "
            f"never into {imp.into}"
        )
    provider, reason = _provider_manifest(root / VENDOR_DIR)
    if provider is None:
        return (
            f"the pinned copy has no manifest to check the import against ({reason}): take "
            "beethoven-qa 2.0.0 or later"
        )
    wanted = (imp.export, imp.version)
    export = next((e for e in provider.exports if (e.id, e.version) == wanted), None)
    if export is None:
        listed = ", ".join(sorted(f"{e.id} {e.version}" for e in provider.exports))
        return (
            f"the pinned copy of beethoven-qa does not offer {imp.export} version "
            f"{imp.version} (it offers {listed})"
        )
    absent = [p for p in export.paths if not _carried(p)]
    if absent:
        return f"the pinned copy does not carry the {imp.export} export: {', '.join(absent)}"
    return ""


def _carried(path: str) -> bool:
    # What the vendor command writes into the copy: its roots, and the kit under kit/.
    roots = tuple(f"{root}/" for root in COPY_ROOTS)
    return path in KIT or path.rstrip("/") in COPY_ROOTS or covered(path, roots)


def _record(imp: Import, record: Recorded) -> list[Breach]:
    said: list[str] = []
    if record.repository != imp.repository:
        said.append(f"names {record.repository or 'no repository'}, not {imp.repository}")
    if not record.commit:
        said.append("names no commit (a full 40-character SHA on its second line)")
    said += [
        f"says export {e} {v}, and the import is {imp.export} {imp.version}"
        for e, v in record.exports
        if (e, v) != (imp.export, str(imp.version))
    ]
    said += [f"cannot read the line {line!r}" for line in record.unread]
    return [Breach(imp.origin, 0, 3, imp.origin, f"ORIGIN {message}") for message in said]


def _present(folder: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (rel, hashlib.sha256((folder / rel).read_bytes()).hexdigest())
        for rel in _files_under(folder)
        if rel not in _FOLDER_FILES
    )


def _pinned(repo: _Repo, imp: Import) -> list[Breach]:
    if imp.repository == _QA:
        return _qa_copy(repo, imp)
    folder, origin = repo.root / imp.into, repo.root / imp.origin
    if not folder.is_dir() or not origin.is_file():
        return [
            Breach(
                imp.origin,
                0,
                3,
                imp.into,
                f"the import of {imp.export} from {imp.repository} has no folder with its "
                "ORIGIN; take it with its pull.sh",
            )
        ]
    record = read_origin(origin.read_text(encoding="utf-8", errors="replace"))
    difference = compare_copy(record.files, _present(folder))
    kinds = (
        (difference.missing, "is recorded in ORIGIN and missing"),
        (difference.extra, "is in the folder and not recorded in ORIGIN"),
        (difference.changed, "differs from the bytes ORIGIN records"),
    )
    return _record(imp, record) + [
        Breach(f"{imp.into}/{rel}", 0, 3, rel, what) for rels, what in kinds for rel in rels
    ]


# --- rules 4 and 5 --------------------------------------------------------------------------------


class _Use(NamedTuple):
    # One import of another engine package: where, the module, the names (none for `import`).
    path: str
    line: int
    module: str
    names: tuple[str, ...]


def _uses(repo: _Repo, rel: str) -> tuple[list[_Use], list[Breach]]:
    own = dict(ENGINE).get(_own(repo))
    try:
        tree = ast.parse((repo.root / rel).read_bytes(), filename=rel)
    except (SyntaxError, ValueError) as exc:
        return [], [
            Breach(
                rel, 0, 4, rel, f"cannot be read as Python, so its imports cannot be checked: {exc}"
            )
        ]
    found: list[_Use] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [_Use(rel, node.lineno, a.name, ()) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(_Use(rel, node.lineno, node.module, tuple(a.name for a in node.names)))
    return [u for u in found if u.module.split(".")[0] in _PROVIDER.keys() - {own}], []


def _python_surface(folder: Path) -> dict[str, tuple[str, ...]] | str | None:
    # The python export, module -> names, of the provider whose manifest is in ``folder`` (its own
    # layout, interface/ at the top); why it cannot be read; or None when no manifest is there.
    theirs, _ = _provider_manifest(folder)
    if theirs is None:
        return None
    export = next((e for e in theirs.exports if e.kind == "python"), None)
    listed = [
        p for p in (export.paths if export else ()) if p != MANIFEST and covered(p, _SURFACE_FILE)
    ]
    if len(listed) != 1:
        return f"{folder}/{MANIFEST}, which lists no python export with one file under interface/"
    try:
        return _SURFACE.validate_json((folder / listed[0]).read_bytes())
    except (OSError, ValidationError) as exc:
        return f"{folder}/{listed[0]}, which cannot be read ({exc})"


def _surface(repo: _Repo, manifest: Manifest, provider: str) -> dict[str, tuple[str, ...]] | str:
    # From this repository's pinned copy of the provider: beethoven-qa's own copy, or an import's.
    folders = [VENDOR_DIR] if provider == _ORDER[0] else []
    folders += [i.into for i in manifest.imports if i.repository == f"{OWNER}/{provider}"]
    for folder in dict.fromkeys(folders):
        surface = _python_surface(repo.root / folder)
        if surface is not None:
            return surface
    return f"no pinned copy of the manifest of {provider} (interface/INTERFACE.json)"


def _published(repo: _Repo, manifest: Manifest, uses: list[_Use]) -> list[Breach]:
    found: list[Breach] = []
    surfaces: dict[str, dict[str, tuple[str, ...]] | str] = {}
    for use in uses:
        provider = _provider(use)
        if provider not in surfaces:
            surfaces[provider] = _surface(repo, manifest, provider)
        found += _off_surface(use, provider, surfaces[provider])
    return found


def _off_surface(
    use: _Use, provider: str, surface: dict[str, tuple[str, ...]] | str
) -> list[Breach]:
    # A surface that could not be read is the reason, given as a string.
    if isinstance(surface, str):
        message = f"imports {use.module}, and there is {surface}"
        return [Breach(use.path, use.line, 4, use.module, message)]
    return [
        Breach(
            use.path,
            use.line,
            4,
            name,
            f"imports {name}, which the python export of {provider} does not list",
        )
        for name in unpublished(use.module, use.names, surface)
    ]


def _all_uses(repo: _Repo) -> tuple[list[_Use], list[Breach]]:
    uses: list[_Use] = []
    unreadable: list[Breach] = []
    for rel in (r for r in _ours(repo) if r.endswith(".py")):
        more, cannot = _uses(repo, rel)
        uses += more
        unreadable += cannot
    return uses, unreadable


def _provider(use: _Use) -> str:
    return _PROVIDER[use.module.split(".")[0]]


def _engine(repo: _Repo, manifest: Manifest) -> list[Breach]:
    # Rule 5 over the manifest's imports and the Python imports of the engine's packages, then
    # rule 4 over the Python imports that go the right way.
    uses, found = _all_uses(repo)
    named = tuple(i.repository.removeprefix(f"{OWNER}/") for i in manifest.imports)
    found += [
        Breach(MANIFEST, 0, 5, name, f"{manifest.name} imports {name}")
        for name in against_direction(manifest.name, named)
    ]
    against = set(against_direction(manifest.name, tuple(_provider(u) for u in uses)))
    found += [
        Breach(u.path, u.line, 5, u.module, f"{manifest.name} imports {u.module}")
        for u in uses
        if _provider(u) in against
    ]
    return found + _published(repo, manifest, [u for u in uses if _provider(u) not in against])


# --- the report -----------------------------------------------------------------------------------


def _say(breach: Breach) -> str:
    where = f"{breach.path}:{breach.line}" if breach.line else breach.path
    return f"boundary: {where}: rule {breach.rule}: {breach.message}"


def _print_known(known: dict[str, dict[str, str]]) -> None:
    # Every run says what it lets stand and why, so an exception is never silent.
    for path, entries in sorted(known.items()):
        for what, why in sorted(entries.items()):
            print(f"boundary: known exception, {path}: {what}: {why}")


def _report(repo: _Repo, found: list[Breach]) -> int:
    known = {(path, what) for path, entries in repo.known.items() for what in entries}
    excusable = {(b.path, b.what) for b in found if b.rule in (1, 4)}
    left = [b for b in found if b.rule not in (1, 4) or (b.path, b.what) not in known]
    stale = stale_exceptions(frozenset(excusable), frozenset(known))
    _print_known(repo.known)
    for breach in left:
        print(_say(breach))
    for path, what in stale:
        print(
            f"boundary: [tool.beethoven-qa.boundary] {path} {what!r} matches nothing any more: "
            "remove the entry"
        )
    for rule in sorted({b.rule for b in left}):
        print(f"boundary: {_ADVICE[rule]}")
    count = len(left) + len(stale)
    if count:
        print(f"boundary: {count} finding{'' if count == 1 else 's'} in {repo.root}")
        return 1
    print(f"boundary: rules 1 to 5 hold in {repo.root} ({len(known)} known exceptions)")
    return 0


def _run(root: Path) -> int:
    # The five rules on the repository at ``root``: 0 when they hold, 1 on a finding, 2 when they
    # cannot be checked here at all. Private and uncontracted: it reads the tree and asks git.
    root = root.resolve()
    try:
        repo, found = _load(root)
    except _CannotRun as reason:
        print(f"boundary: {reason}")
        return 2
    found += _sealed(repo)
    manifest = repo.manifest
    if manifest is None:
        print("boundary: rules 3 to 5 read the manifest, so they wait until it can be read")
        return _report(repo, found)
    if any(i.repository == _QA for i in manifest.imports):
        print(
            f"boundary: {VENDOR_DIR} is beethoven-qa's pinned copy; vendor-check checks its files"
        )
    found += _declared(repo, manifest)
    found += [breach for imp in manifest.imports for breach in _pinned(repo, imp)]
    return _report(repo, found + _engine(repo, manifest))
