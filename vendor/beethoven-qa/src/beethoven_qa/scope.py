"""Which modules of the package a change reaches, so the gate proves only those (``--base``).

CrossHair's verdict on a target is decided by the module that defines it, by every module of the
package that module imports, directly or through others (CrossHair runs the body of every function
a target calls), by the packages they sit in (whose ``__init__`` runs first), and by what every
proof reads: ``pyproject.toml`` (the ``[tool.beethoven-qa]`` table and its bound), the locked
environment (``pixi.toml``, ``pixi.lock``) and the gate's own code. A change that reaches none of
those leaves the verdict as it was at the base commit, so with a base the gate proves again only
the modules the change reaches:

- a change to ``pixi.toml`` or ``pixi.lock``, to the gate's own files (the ``__init__``,
  ``contracts``, ``gate``, ``scope`` and ``_crosshair`` of the ``beethoven_qa`` the gate runs
  from, wherever that copy sits), to any other file of the repository the gate's process
  imported from outside the package, to a file of the package's directory that is not one of its
  modules (a stub, a data file, a cached ``.pyc`` git tracks) or to a Python file of the source
  directory outside the package (a source, a stub or a compiled file) reaches every module;
- a link or a submodule git tracks anywhere in the repository, at the base or in the index, and a
  link on the disk among the root files or in the package's directory (the entry itself or a
  folder on its way), prove every module whatever changed (from 10.0.0), since git names an edit
  made through one by a path that says nothing of what a proof reads;
- a change to ``pyproject.toml`` is read by its keys, not its bytes (from 10.0.0): a comment, a
  blank line or a key no proof reads reaches nothing, which is an entry of
  ``[tool.beethoven-qa.unproven]`` added or reworded (listing keeps a function from being a target
  and nothing more), an entry of ``[tool.beethoven-qa.boundary]`` (only the boundary lane reads
  it) and the table of a tool in ``QUIET_TOOLS`` (a lane of its own, which the CrossHair process
  never imports); an unproven entry removed reaches the one target it named, a target again; any
  other key reaches every module, the rest of the gate's table, ``[project]``, ``[build-system]``
  and the table of a tool the gate does not know among them, and so does a file either side of
  which cannot be read as TOML;
- a change to a module reaches that module and every module that imports it, directly or through
  others, a package's ``__init__`` counting as imported by every module under it;
- a module that imports by a name it computes (``importlib.import_module`` or ``__import__`` given
  anything but an absolute name as a string, ``exec``, ``eval``, ``runpy``, a loader by path), or
  by a star (``from x import *``, which runs whatever submodules ``x``'s ``__all__`` names), is
  reached by every change to the package, since its text does not say what it imports;
- anything else (documents, data, tests, workflows, the kit) reaches no module.

Check 1 of the gate (contracts, imports, directives, suppressions, skipped tests, the unproven
entries) is cheap and always covers the whole package; only check 2, the proof, is narrowed. When
the base cannot be told, or git fails, every module is proven, so the narrowing can only ever prove
more than it must, never less than the rule above. What it assumes is that the modules a change does
not reach passed at the base: the pre-push hook's base is where the branch left the remote's default
branch, which that branch's own required check passed, and a CI run names the newest commit whose
gate passed on a push. It assumes too that a proof reads no file but those above: a contracted
function is pure, as the layering rule asks (the definition-of-done skill), so a data file outside
the package's directory decides no verdict, and nothing it runs imports inside a function body: a
module outside the package that the package imports at its top is a file the gate's process
imported, which every proof reads, while one imported only inside a function (at the
repository's root, say, which a CrossHair process started there finds) is a file the narrowing
never sees. Of ``pyproject.toml`` it assumes that only the gate's
table is read during a proof, and that by the gate, never by CrossHair, icontract, beartype or a
pure function: a comment and a quiet tool's table then decide nothing, and a key of a table the gate
does not know proves everything, so a reader it has not heard of costs time, never a verdict.
CrossHair's search is bounded and timed, so it is not quite the same twice; the full run, with no
base (the default), stays the backstop.

The decisions carry contracts and are proven with the rest of the package; reading git and the
files is private and carries none, as in the gate itself. ``_resolve`` is the standards' base too.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from beartype import beartype

from beethoven_qa.contracts import ensure
from beethoven_qa.vendor import _git, _git_bytes

__all__ = [
    "QUIET_TOOLS",
    "ROOT_FILES",
    "TABLE_FILE",
    "Effect",
    "differing_keys",
    "module_named",
    "reached",
    "reaches_every_module",
    "table_reach",
]

# The files at the repository's root that every proof reads: the gate's table and its bound, and
# the environment CrossHair, Z3 and the package's dependencies come from. The table, TABLE_FILE, is
# read by its keys (table_reach); the other two by their bytes.
ROOT_FILES = frozenset({"pyproject.toml", "pixi.toml", "pixi.lock"})
TABLE_FILE = "pyproject.toml"

# The tools whose tables in pyproject.toml no proof reads: each is a lane of its own or a checker a
# repository runs beside the gate (lint, typecheck, test and the security group read theirs), and
# the CrossHair process imports none of them. A tool not named here, one the gate does not know
# among them, reaches every module, since leaving one out only proves more. coverage.py is left out
# on purpose: its .pth file starts it in every Python process where COVERAGE_PROCESS_START is set,
# the CrossHair processes among them, and its table can name plugins, which are code.
QUIET_TOOLS = frozenset(
    {
        "bandit",
        "black",
        "codespell",
        "flake8",
        "isort",
        "mypy",
        "pylint",
        "pyright",
        "pytest",
        "ruff",
    }
)

_UNPROVEN = ("tool", "beethoven-qa", "unproven")
_BOUNDARY = ("tool", "beethoven-qa", "boundary")

# What a Python file of the source directory may be, beside the package: a module, a stub that
# mypy and an import would read in its place, or a compiled file an import loads in place of its
# source (a hash-based .pyc whatever code it holds, a sourceless one alone).
_PYTHON = (".py", ".pyi", ".pyc")


def _source_prefix(src: str) -> str:
    # The prefix every file of the source directory starts with, as git prints a path: nothing
    # when the source directory is the repository's root.
    parts = [part for part in src.strip("/").split("/") if part not in ("", ".")]
    return "".join(f"{part}/" for part in parts)


@beartype
@ensure(
    lambda result, package: result is None or result == package or result.startswith(package + "."),
    "only a module of the package is named",
)
@ensure(
    lambda result: result is None or all(part.isidentifier() for part in result.split(".")),
    "a name an import can take",
)
@ensure(lambda result, path: result is None or path.endswith(".py"), "only a Python file is one")
def module_named(path: str, src: str, package: str) -> str | None:
    """The module of ``package`` the repository-relative file ``path`` holds, by the name an import
    gives it (an ``__init__.py`` holds its package), or ``None`` when it holds none: not under
    ``<src>/<package>/``, not a ``.py`` file, or a name no import can take.
    """
    prefix = f"{_source_prefix(src)}{package}/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    parts = [package, *path[len(prefix) : -len(".py")].split("/")]
    if parts[-1] == "__init__":
        parts.pop()
    if not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


@beartype
@ensure(
    lambda result, path, everywhere: result or path not in everywhere,
    "a file every proof reads reaches every module",
)
@ensure(
    lambda result, path, src, package, everywhere: (
        not result or path in everywhere or module_named(path, src, package) is None
    ),
    "a module of the package reaches its importers, never every module on its own",
)
@ensure(
    lambda result, path, src, everywhere: (
        not result or path in everywhere or path.startswith(_source_prefix(src))
    ),
    "outside the source directory only a file every proof reads reaches every module",
)
def reaches_every_module(path: str, src: str, package: str, everywhere: frozenset[str]) -> bool:
    """Whether a change to the repository-relative file ``path`` can change the verdict on every
    module: a file every proof reads (``everywhere``: the root files, the gate's own, what the
    gate imported from outside the package), any file of the package's directory that is not one
    of its modules, or a Python file of the source directory outside the package.
    """
    if path in everywhere:
        return True
    in_package = path.startswith(f"{_source_prefix(src)}{package}/")
    in_source = path.startswith(_source_prefix(src)) and path.endswith(_PYTHON)
    return (in_package or in_source) and module_named(path, src, package) is None


@beartype
@ensure(lambda result, imports: result <= imports.keys(), "only modules of the package")
@ensure(lambda result, changed, imports: changed & imports.keys() <= result, "each changed one")
@ensure(
    lambda result, imports: all(m in result for m, names in imports.items() if names & result),
    "every module that imports a reached one is reached",
)
@ensure(
    lambda result, changed, imports, dynamic: not changed or dynamic & imports.keys() <= result,
    "a module that computes its imports is reached by every change",
)
@ensure(lambda result, changed: changed or not result, "no change reaches nothing")
def reached(
    changed: frozenset[str], imports: dict[str, frozenset[str]], dynamic: frozenset[str]
) -> frozenset[str]:
    """The modules whose verdict a change to the modules ``changed`` can alter: each of them
    (``imports`` names every module of the package with the names its text imports, the packages
    above it among them), every module that imports a reached one, through any number of others,
    and every module of ``dynamic``, which imports by names it computes, once anything changed.
    A changed name the graph does not hold (a module deleted) still reaches what imports it.
    """
    if not changed:
        return frozenset()
    found = set((changed | dynamic) & imports.keys())
    frontier = changed | frozenset(found)
    while frontier:
        found_now = {m for m, names in imports.items() if m not in found and names & frontier}
        found |= found_now
        frontier = frozenset(found_now)
    return frozenset(found)


@beartype
@ensure(lambda result, base, head: base.keys() ^ head.keys() <= result, "a key of one side differs")
@ensure(
    lambda result, base, head: all(
        (key in result) == (base[key] != head[key]) for key in base.keys() & head.keys()
    ),
    "a key of both sides differs exactly when its two values do",
)
@ensure(lambda result, base, head: result <= base.keys() | head.keys(), "only a key of a side")
def differing_keys(
    base: dict[tuple[str, ...], str], head: dict[tuple[str, ...], str]
) -> frozenset[tuple[str, ...]]:
    """The key paths whose value differs between ``base`` and ``head``, each a table of
    ``pyproject.toml`` flattened to its leaves: a path one side lacks, or whose two values differ.
    A path is a tuple of keys, so a key that holds a dot never splits.
    """
    return frozenset(key for key in base.keys() | head.keys() if base.get(key) != head.get(key))


class Effect(StrEnum):
    """What a change to one key path of ``pyproject.toml`` reaches: no target, the one target an
    unproven entry named, or every module."""

    NONE = "none"
    TARGET = "target"
    EVERY = "every"


def _quiet_tool(path: tuple[str, ...]) -> bool:
    return len(path) >= 2 and path[0] == "tool" and path[1] in QUIET_TOOLS


@beartype
@ensure(
    lambda result, path: result[0] != Effect.TARGET or (len(path) == 4 and path[:3] == _UNPROVEN),
    "only an unproven entry names a target",
)
@ensure(
    lambda result, path: result[1] == (path[3] if result[0] == Effect.TARGET else ""),
    "the target named is the one the entry named, and nothing else names one",
)
@ensure(
    lambda result, path, after: (
        not (len(path) == 4 and path[:3] == _UNPROVEN and not after) or result[0] == Effect.TARGET
    ),
    "an entry removed reaches the target it named",
)
@ensure(
    lambda result, path: (
        result[0] != Effect.NONE or path[:3] in (_UNPROVEN, _BOUNDARY) or _quiet_tool(path)
    ),
    "only the unproven and boundary entries and the quiet tools' tables reach nothing",
)
@ensure(
    lambda result, path: (
        path[:2] != ("tool", "beethoven-qa")
        or path[2:3] in (("unproven",), ("boundary",))
        or result[0] == Effect.EVERY
    ),
    "the rest of the gate's table reaches every module",
)
@ensure(
    lambda result, path: (
        not (path[:3] == _BOUNDARY or _quiet_tool(path)) or result[0] == Effect.NONE
    ),
    "the boundary entries and a quiet tool's table reach nothing",
)
@ensure(
    lambda result, path, after: (
        not (path[:3] == _UNPROVEN and (len(path) == 3 or (len(path) == 4 and after)))
        or result[0] == Effect.NONE
    ),
    "an unproven entry added or reworded, or the table itself, reaches nothing",
)
def table_reach(path: tuple[str, ...], after: bool) -> tuple[Effect, str]:
    """What a change to the key path ``path`` of ``pyproject.toml`` reaches, ``after`` saying
    whether the head still holds the path: an unproven entry added or reworded reaches nothing,
    one removed the target it named; the boundary entries and a quiet tool's table nothing;
    anything else, the rest of the gate's table among it, every module.
    """
    if path[:3] == _BOUNDARY or _quiet_tool(path):
        return Effect.NONE, ""
    if path[:3] == _UNPROVEN and len(path) <= 4:
        if len(path) == 4 and not after:
            return Effect.TARGET, path[3]
        return Effect.NONE, ""
    return Effect.EVERY, ""


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


def _changed(root: Path, base: str) -> tuple[str, ...]:
    # Every file the working tree holds otherwise than the base commit, a renamed one under both of
    # its names (a module renamed is one gone and one new), and every file git does not track yet,
    # but an untracked one under __pycache__, which the gate's own import of the package writes
    # before the change is read. A tracked one counts: a hash-based .pyc is loaded in place of its
    # source whatever code it holds, so git's copy of one is what a proof may run.
    diff = _git(root, "diff", "--no-renames", "--no-ext-diff", "--name-only", "-z", base)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    tracked = {path for path in diff.split("\x00") if path}
    fresh = {path for path in untracked.split("\x00") if path}
    return tuple(sorted(tracked | {p for p in fresh if "__pycache__" not in p.split("/")}))


# The modes git gives a link and a submodule, whose entries say nothing of the files a proof reads
# through them.
_LINK_MODES = {"120000": "a link", "160000": "a submodule"}


def _tracked_link(root: Path, commit: str) -> str | None:
    # Why every module is proven when git tracks a link or a submodule anywhere, at the base or in
    # the index, else None: git names an edit made through a link by the link's target, and one
    # inside a submodule by the submodule alone, so where such a path sits says nothing of whether
    # a proof reads it. A link nobody tracks is a change git lists under its own name.
    base = _git(root, "ls-tree", "-r", "-z", commit)
    index = _git(root, "ls-files", "--stage", "-z")
    for entry in (base + index).split("\x00"):
        mode, _, rest = entry.partition(" ")
        if mode in _LINK_MODES:
            what, path = _LINK_MODES[mode], rest.partition("\t")[2]
            return (
                f"{path} is {what} git tracks, so the path git names for an edit made through "
                "it says nothing of what a proof reads"
            )
    return None


def _leaves(document: dict[str, object]) -> dict[tuple[str, ...], str]:
    # Every key path of a parsed TOML document down to a value that is not a table, or to an empty
    # table, with the value as text: an array is one value, compared whole. Walked with a stack, not
    # by recursion, since a dotted key may nest tables deeper than Python recurses.
    found: dict[tuple[str, ...], str] = {}
    stack: list[tuple[tuple[str, ...], object]] = [((), document)]
    while stack:
        prefix, value = stack.pop()
        if isinstance(value, dict) and value:
            stack.extend(((*prefix, str(key)), inner) for key, inner in value.items())
        else:
            found[prefix] = repr(value)
    return found


def _spelled(path: tuple[str, ...]) -> str:
    # A key path as TOML writes it, a key that is not a bare one in quotes.
    bare = re.compile(r"[A-Za-z0-9_-]+")
    return ".".join(part if bare.fullmatch(part) else f'"{part}"' for part in path)


def _tables(
    root: Path, commit: str
) -> tuple[dict[tuple[str, ...], str], dict[tuple[str, ...], str]]:
    # The table file's key paths at the base and at the head; ValueError naming the side that
    # cannot be read (absent, not UTF-8, not TOML). git's own error repeats its whole command, the
    # checkout's path among it, so it is not passed on.
    at = f"{TABLE_FILE} cannot be read at {commit[:12]}"
    try:
        before = _git_bytes(root, "show", f"{commit}:{TABLE_FILE}").decode("utf-8")
    except subprocess.CalledProcessError:
        raise ValueError(f"{at}: git cannot show it there") from None
    except ValueError as exc:
        raise ValueError(f"{at}: {exc}") from None
    try:
        base = _leaves(tomllib.loads(before))
        head = _leaves(tomllib.loads((root / TABLE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError) as exc:
        # RecursionError: tomllib parses an array or an inline table by recursion, and gives up
        # on one nested past Python's limit.
        raise ValueError(f"{TABLE_FILE} cannot be read as TOML at a side: {exc}") from None
    return base, head


def _table_change(root: Path, commit: str) -> tuple[str, frozenset[str]]:
    # What the change since ``commit`` to the table file reaches (table_reach): why it reaches
    # every module, empty when it does not, and the targets its removed unproven entries named.
    try:
        base, head = _tables(root, commit)
    except ValueError as exc:
        return str(exc), frozenset()
    named: set[str] = set()
    for path in sorted(differing_keys(base, head)):
        effect, target = table_reach(path, path in head)
        if effect == Effect.EVERY:
            return f"{TABLE_FILE}'s {_spelled(path)} changed since {commit[:12]}", frozenset()
        if effect == Effect.TARGET:
            named.add(target)
    return "", frozenset(named)


class _Graph(NamedTuple):
    # Every module of the package's directory, with the names under the package its text imports
    # (the packages above it among them), and the modules that import by a name they compute.
    imports: dict[str, frozenset[str]]
    dynamic: frozenset[str]


# What a call may be named when it imports, or runs code, by a name the text does not hold: a
# builtin called by its own name, or the function of a module (importlib, runpy, a spec's loader).
_COMPUTED_NAMES = frozenset({"__import__", "exec", "eval", "import_module", "run_module"})
_COMPUTED_ATTRIBUTES = frozenset(
    {
        "__import__",
        "import_module",
        "run_module",
        "run_path",
        "spec_from_file_location",
        "exec_module",
        "load_module",
    }
)


def _anchor(module: str, is_package: bool, level: int) -> str | None:
    # The package a relative import with ``level`` dots starts from, or None above the top.
    parts = module.split(".") if is_package else module.split(".")[:-1]
    keep = len(parts) - (level - 1)
    return ".".join(parts[:keep]) if keep >= 1 else None


def _from_names(node: ast.ImportFrom, module: str, is_package: bool) -> list[str]:
    # The module a `from` import reads, and what it names in it, any of which may be a submodule.
    start = _anchor(module, is_package, node.level) if node.level else ""
    if start is None:
        return []
    where = ".".join(part for part in (start, node.module or "") if part)
    return [where, *(f"{where}.{alias.name}" for alias in node.names if alias.name != "*")]


def _literal(call: ast.Call) -> str | None:
    # The absolute module name a call to import_module or __import__ gives as a plain string.
    first = call.args[0] if call.args else None
    text = first.value if isinstance(first, ast.Constant) else None
    if isinstance(text, str) and text and not text.startswith(".") and len(call.args) == 1:
        return text
    return None


def _called(call: ast.Call) -> str | None:
    # The name a call is made by, when it is one that imports or runs code by a computed name.
    func = call.func
    if isinstance(func, ast.Name) and func.id in _COMPUTED_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _COMPUTED_ATTRIBUTES:
        return func.attr
    return None


def _call_names(call: ast.Call) -> tuple[list[str], bool]:
    # What one call imports by a name its text holds, and whether it imports by a computed one.
    called = _called(call)
    if called is None:
        return [], False
    literal = _literal(call) if called in ("import_module", "__import__") else None
    return ([literal], False) if literal else ([], True)


def _node_names(node: ast.AST, module: str, is_package: bool) -> tuple[list[str], bool]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names], False
    if isinstance(node, ast.ImportFrom):
        star = any(alias.name == "*" for alias in node.names)
        return _from_names(node, module, is_package), star
    if isinstance(node, ast.Call):
        return _call_names(node)
    return [], False


def _with_parents(name: str) -> list[str]:
    # A dotted name and every package above it, each of which an import of it runs.
    parts = name.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts) + 1)]


def _edges(
    module: str, is_package: bool, tree: ast.Module, package: str
) -> tuple[frozenset[str], bool]:
    # The names under the package one module's text imports, anywhere in it (a function, a
    # TYPE_CHECKING block, a try), each with the packages above it, the module's own packages
    # among them; and whether it imports by a computed name.
    names = set(_with_parents(module)[:-1])
    computes = False
    for node in ast.walk(tree):
        found, computed = _node_names(node, module, is_package)
        computes = computes or computed
        names.update(n for name in found for n in _with_parents(name))
    inside = {n for n in names if n == package or n.startswith(f"{package}.")}
    return frozenset(inside - {module}), computes


def _graph(root: Path, src: str, package: str) -> _Graph:
    # Every module of the package's directory, read from its file whether or not it imports (the
    # gate reports a failed import itself); a file that cannot be parsed counts as importing by a
    # computed name, since nothing says what it imports.
    imports: dict[str, frozenset[str]] = {}
    dynamic: set[str] = set()
    home = root / f"{_source_prefix(src)}{package}"
    for path in sorted(home.rglob("*.py")):
        module = module_named(path.relative_to(root).as_posix(), src, package)
        if module is None or "__pycache__" in path.parts:
            continue
        is_package = path.name == "__init__.py"
        try:
            tree = ast.parse(path.read_bytes(), str(path))
        except (SyntaxError, ValueError):
            imports[module], computes = frozenset(_with_parents(module)[:-1]), True
        else:
            imports[module], computes = _edges(module, is_package, tree, package)
        if computes:
            dynamic.add(module)
    return _Graph(imports, frozenset(dynamic))
