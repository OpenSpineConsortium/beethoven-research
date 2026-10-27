"""The symbolic verification gate (definition of done, step 4), for any repository that carries this
package.

Two checks; both always run, and the gate fails on either:

1. Every public function of the repository's package carries icontract contracts, and nothing
   keeps a function out of CrossHair's sight: every ``.py`` file under the package is imported as
   one of its modules (and imports without exiting), no ``# crosshair:`` directive turns checking
   off or cuts it short, and no public coroutine waits to be proven (CrossHair would check the
   un-awaited coroutine object). A package with nothing to prove is a failure, not a pass: a gate
   that only ever says "no counterexample" because there was nothing to check is the
   wrong-but-plausible failure this project guards against. Nothing sets a finding aside either:
   no comment of the kind the standards' tools read (``# noqa``, ``# type: ignore``, ``# nosec``,
   ``# pragma: no cover``, ``# jscpd:ignore``, ``# zizmor: ignore``, ``# complexipy: ignore``,
   lizard's ``#lizard forgive`` and ``GENERATED CODE``), and
   no test skipped or expected to fail (pytest's and unittest's marks and calls), in the package
   and in the repository's ``tests/`` directory, since the standards honour no such comment and a
   test that does not run proves nothing. A string that holds such text is not a finding.
2. CrossHair + Z3 find no input that satisfies the preconditions and violates a postcondition,
   within the per-condition timeout, for every contracted function and every class that carries
   contracts. CrossHair exits 1 on a counterexample and prints it; that is the SpecPylot feedback
   the ``verify-module`` workflow reads back. Every run prints every condition's verdict
   (``Confirmed over all paths.``, ``Not confirmed.`` within the bound, ``Unable to meet
   precondition.``), counts them under each target and over the run (``verdicts``), and fails on a
   precondition no input can meet: either the contract is vacuous, or it is stated over values
   CrossHair cannot build. ``--report-all`` is accepted and changes nothing; it used to be the only
   way to see the verdicts, and the plain gate the hook and CI ran passed such a precondition in
   silence.

A function CrossHair must not run (it reads, writes, asks the clock or starts a process) or cannot
run (a measured fault of CrossHair's own) is named, with its reason, under
``[tool.beethoven-qa.unproven]``: the gate never checks it as a target, and prints every entry with
its reason on every run, so what is not proven is never hidden in a comment the way a directive
would hide it. Listing does nothing more than that. CrossHair runs the body of every function a
contracted target calls, listed or not, and assumes the callee's contracts rather than checking
them, so a contracted caller of a listed function does the same I/O during the gate and is itself
kept private, exempt or listed, and a listed function's contracts, assumed by every proven caller,
are checked by the tests alone. An entry names a function, never a class or a method: a class is
proven whole, as one target, and I/O stays out of contracted classes.

``--module`` restricts both checks to one module of the package, which is how the workflows prove
the module they are working on while the rest of the package is still being brought up to the bar.

What the gate checks is named in the repository's ``pyproject.toml`` under ``[tool.beethoven-qa]``
(the package, its source directory, the modules allowed to carry no contracts, the functions set
aside from the proof, the per-condition timeout), so beethoven-qa, beethoven-agents and the
research pipeline run one gate, the same code, each on its own package. It runs as
``python -m beethoven_qa gate`` (``pixi run symbolic``).

Why the module is split the way it is: CrossHair executes every contracted function it checks, with
symbolic arguments, and whatever that function calls. The functions below that carry contracts are
pure (they decide, they never import, read or start anything); the ones that import the package and
start CrossHair are private, carry none, and are called by no contracted function, so the gate
never runs itself symbolically. Both halves matter: the gate names only contracted functions as
targets, but CrossHair runs whatever a target calls.
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import os
import pkgutil
import re
import subprocess
import sys
import tokenize
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from beartype import beartype
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from beethoven_qa.contracts import ensure, require
from beethoven_qa.scope import (
    ROOT_FILES,
    TABLE_FILE,
    _changed,
    _graph,
    _resolve,
    _table_change,
    _tracked_link,
    module_named,
    reached,
    reaches_every_module,
)
from beethoven_qa.vendor import _toplevel, is_safe_relpath

__all__ = [
    "FunctionEntry",
    "GateConfig",
    "Verdicts",
    "crosshair_command",
    "is_dotted_identifier",
    "is_public",
    "set_aside",
    "uncontracted",
    "verdicts",
]

# A dotted Python module path: identifiers separated by single dots, nothing else.
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


@beartype
@ensure(lambda result, name: not result or len(name) > 0, "an empty string is never a module path")
@ensure(lambda result, name: not result or ".." not in name, "no empty component")
@ensure(lambda result, name: not result or not name.startswith("."), "never relative")
def is_dotted_identifier(name: str) -> bool:
    """``True`` when ``name`` is a dotted module path such as ``beethoven_qa.gate``."""
    return _DOTTED.fullmatch(name) is not None


@beartype
@ensure(lambda result, name: result == (len(name) > 0 and not name.startswith("_")), "the rule")
def is_public(name: str) -> bool:
    """A name is public when it is non-empty and does not start with an underscore."""
    return len(name) > 0 and not name.startswith("_")


class FunctionEntry(NamedTuple):
    """One module-level function as the gate found it: where it was found, where it was defined,
    and whether icontract recorded contracts on it."""

    module: str
    name: str
    defined_in: str
    has_contracts: bool


@beartype
@require(
    lambda entries: len({f"{e.module}.{e.name}" for e in entries}) == len(entries),
    "each qualified name appears once, as it does in a module's namespace",
)
@ensure(lambda result: list(result) == sorted(result), "reported in a stable order")
@ensure(
    lambda result, entries: set(result) <= {f"{e.module}.{e.name}" for e in entries},
    "only functions that were found are reported",
)
@ensure(
    lambda result, entries, exempt: all(
        f"{e.module}.{e.name}" not in result
        for e in entries
        if e.has_contracts
        or e.module in exempt
        or e.defined_in != e.module
        or not is_public(e.name)
    ),
    "a contracted, exempt, imported or private function is never reported",
)
@ensure(
    lambda result, entries, exempt: all(
        f"{e.module}.{e.name}" in result
        for e in entries
        if not e.has_contracts
        and e.module not in exempt
        and e.defined_in == e.module
        and is_public(e.name)
    ),
    "every public function defined in a checked module without contracts is reported",
)
def uncontracted(entries: tuple[FunctionEntry, ...], exempt: frozenset[str]) -> list[str]:
    """The qualified names of the public functions that should carry contracts and do not.

    A function counts in the module that defines it; one that a module only imports is checked in
    its own module, so it is never reported twice or under someone else's name.
    """
    return sorted(
        {
            f"{e.module}.{e.name}"
            for e in entries
            if not e.has_contracts
            and e.module not in exempt
            and e.defined_in == e.module
            and is_public(e.name)
        }
    )


@beartype
@require(lambda timeout: timeout >= 1, "a per-condition budget of at least one second")
@require(lambda python: len(python) > 0, "the interpreter to run CrossHair with")
@require(lambda target: len(target) > 0, "the package directory to check")
@require(lambda target: not target.startswith("-"), "a target, never an option")
@ensure(lambda result, python: result[0] == python, "CrossHair runs under the given interpreter")
@ensure(
    lambda result: result[1:3] == ["-m", "beethoven_qa._crosshair"],
    "through the runner that makes icontract's bookkeeping before CrossHair traces anything",
)
@ensure(lambda result: "--analysis_kind=icontract" in result, "the icontract contracts are checked")
@ensure(
    lambda result, timeout: f"--per_condition_timeout={timeout}" in result,
    "the declared bound is the one applied",
)
@ensure(lambda result, target: target in result, "the target is the one checked")
@ensure(lambda result: result[-1] == "--report_all", "every condition's verdict is printed")
def crosshair_command(python: str, target: str, timeout: int) -> list[str]:
    """The CrossHair command line that proves the contracts of ``target`` (a function, a class, a
    module or a directory) within ``timeout`` seconds per condition, and says what it found of
    every condition: without ``--report_all`` CrossHair prints a counterexample and nothing else,
    so a precondition no input meets, and a search that ran to its bound, looked like a proof.
    CrossHair starts through ``beethoven_qa._crosshair``, which says why.
    """
    return [
        python,
        "-m",
        "beethoven_qa._crosshair",
        "check",
        target,
        "--analysis_kind=icontract",
        f"--per_condition_timeout={timeout}",
        "--report_all",
    ]


class Verdicts(NamedTuple):
    """How many conditions CrossHair confirmed over all paths, could not confirm within the bound
    (no counterexample, no proof), and could find no input for (a precondition it could not
    meet). A counterexample is none of these: CrossHair exits 1 on it and the gate fails there."""

    confirmed: int
    unconfirmed: int
    unmet: int


# CrossHair's own line for each condition, ``<file>:<line>: info: <verdict>``.
_CONFIRMED = ": info: Confirmed over all paths."
_UNCONFIRMED = ": info: Not confirmed."
# CrossHair 0.0.111 ends the third with a period when no input met the precondition and with the
# reason in parentheses when the precondition raised ("(possibly because it raised ... ?)", its
# core.py), so that verdict is read by its opening words, the other two by their whole line.
_UNMET = ": info: Unable to meet precondition"


@beartype
@ensure(
    lambda result, text: sum(result) <= text.count(": info: "),
    "only CrossHair's own info lines carry a verdict",
)
@ensure(
    lambda result, text: (
        sum(result)
        == sum(
            1
            for line in text.splitlines()
            if _UNMET in line or line.endswith((_CONFIRMED, _UNCONFIRMED))
        )
    ),
    "every verdict line is counted once, under one verdict, and nothing else is counted",
)
@ensure(
    lambda result, text: (result.unmet > 0) == any(_UNMET in line for line in text.splitlines()),
    "a precondition no input can meet is never missed and never invented, with or without a reason",
)
def verdicts(text: str) -> Verdicts:
    """The verdicts in what one CrossHair process printed, counted from its per-condition lines."""
    lines = text.splitlines()
    unmet = sum(1 for line in lines if _UNMET in line)
    rest = [line for line in lines if _UNMET not in line]
    return Verdicts(
        sum(1 for line in rest if line.endswith(_CONFIRMED)),
        sum(1 for line in rest if line.endswith(_UNCONFIRMED)),
        unmet,
    )


@beartype
@require(
    lambda functions: len(set(functions)) == len(functions),
    "each function once, as a module's namespace binds it",
)
@ensure(
    lambda result, functions, listed: set(result[0]) == set(functions) - set(listed),
    "every function that is not listed is proven, and no listed one",
)
@ensure(lambda result: len(set(result[0])) == len(result[0]), "nothing is proven twice over")
@ensure(
    lambda result, functions, listed: (
        set(result[1]) | set(result[2]) == set(listed) - set(functions)
    ),
    "every listed name that is no function is reported, and only such a name",
)
@ensure(
    lambda result, classes: all(
        any(n == c or n.startswith(c + ".") for c in classes) for n in result[1]
    ),
    "a name reported as a class or a member of one is one",
)
@ensure(
    lambda result, classes: (
        not any(n == c or n.startswith(c + ".") for n in result[2] for c in classes)
    ),
    "a name reported as stale names no class and no member of one",
)
def set_aside(
    functions: tuple[str, ...], classes: tuple[str, ...], listed: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split the contracted functions into those CrossHair proves and the ``unproven`` entries
    that name no function: the ones that name a class or a member of one (a class is proven
    whole, so such an entry would hide every method, and a method is never a target), and the
    stale ones (a function renamed, removed or never contracted: an entry that hides nothing must
    go).
    """
    named = set(listed)
    found = set(functions)
    misnamed = tuple(
        n
        for n in listed
        if n not in found and any(n == c or n.startswith(c + ".") for c in classes)
    )
    stale = tuple(n for n in listed if n not in found and n not in misnamed)
    return tuple(f for f in functions if f not in named), misnamed, stale


class GateConfig(BaseModel):
    """The ``[tool.beethoven-qa]`` table of a repository's ``pyproject.toml``: the boundary where a
    repository tells the gate what to check, validated once, here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package: str
    src: str = "src"
    exempt: tuple[str, ...] = ()
    # Function (``<module>.<function>``, never a class or a method) to the reason the gate never
    # checks it as a target.
    unproven: dict[str, str] = Field(default_factory=dict)
    per_condition_timeout: PositiveInt = Field(default=20, le=600)
    # File (relative to the root) to what matched there to the reason the boundary check lets that
    # finding of rule 1 or 4 stand (boundary.py): a person's decision in a reviewed change.
    boundary: dict[str, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> GateConfig:
        if not is_dotted_identifier(self.package) or "." in self.package:
            raise ValueError(f"package={self.package!r} is not a top-level package name")
        if self.src.startswith("/") or ".." in Path(self.src).parts:
            raise ValueError(f"src={self.src!r} must be a directory inside the repository")
        for module in self.exempt:
            if module != self.package and not module.startswith(self.package + "."):
                raise ValueError(f"exempt module {module!r} is not part of {self.package!r}")
        for name, why in self.unproven.items():
            if not is_dotted_identifier(name) or not name.startswith(self.package + "."):
                raise ValueError(f"unproven entry {name!r} is not a function of {self.package!r}")
            if not why.strip():
                raise ValueError(f"unproven entry {name!r} needs its reason")
        return self

    @field_validator("boundary")
    @classmethod
    def _known(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for path, entries in value.items():
            if not is_safe_relpath(path) or not entries:
                raise ValueError(f"boundary entry {path!r} is not a file with what matched there")
            if not all(what and why.strip() for what, why in entries.items()):
                raise ValueError(f"boundary entry {path!r} needs what matched and the reason")
        return value

    @classmethod
    def from_pyproject(cls, root: Path) -> GateConfig:
        """Read and validate the table; a repository without one is a configuration error."""
        with (root / "pyproject.toml").open("rb") as fh:
            data = tomllib.load(fh)
        table = data.get("tool", {}).get("beethoven-qa")
        if table is None:
            raise ValueError(f"{root / 'pyproject.toml'} has no [tool.beethoven-qa] table")
        return cls.model_validate(table)


def _has_contracts(fn: object) -> bool:
    # icontract records its contracts as attributes on the function it returns. beartype wraps
    # outside it (contracts.py), and functools' wrappers (cache, lru_cache, wraps) copy the
    # function's attributes onto theirs, so the outermost object is the one to inspect; measured,
    # not assumed, see contracts.py.
    return getattr(fn, "__preconditions__", None) is not None or hasattr(fn, "__postconditions__")


def _unwrap(obj: object) -> object:
    # The function under the wrappers: functools' __wrapped__ chain, and a partial's own function.
    while True:
        inner = getattr(obj, "__wrapped__", None) or getattr(obj, "func", None)
        if inner is None or inner is obj or not callable(inner):
            return obj
        obj = inner


def _is_async(obj: object) -> bool:
    inner = _unwrap(obj)
    return inspect.iscoroutinefunction(inner) or inspect.isasyncgenfunction(inner)


def _class_has_contracts(cls: type) -> bool:
    if getattr(cls, "__invariants__", None):
        return True
    for member in vars(cls).values():
        if isinstance(member, (staticmethod, classmethod)):
            member = member.__func__
        if isinstance(member, property):
            if any(_has_contracts(f) for f in (member.fget, member.fset, member.fdel) if f):
                return True
        elif callable(member) and _has_contracts(member):
            return True
    return False


class _Found(NamedTuple):
    # What the import of the package found: the modules that imported, the name of every module
    # the walk yielded (imported or not: a module that failed keeps its place in the tree, so its
    # entries stay its own), and what kept part of it out of sight.
    modules: list[ModuleType]
    names: list[str]
    problems: list[str]


def _modules(config: GateConfig, root: Path) -> _Found:
    src = (root / config.src).resolve()
    home = src / config.package
    sys.path.insert(0, str(src))
    problems: list[str] = []
    modules: list[ModuleType] = []

    def load(name: str) -> ModuleType | None:
        try:
            return importlib.import_module(name)
        except SystemExit as exc:
            problems.append(
                f"importing {name} exited ({exc.code!r}); guard the call with "
                '`if __name__ == "__main__":`'
            )
        except Exception as exc:  # every import failure is reported, never passed
            problems.append(f"importing {name} failed: {type(exc).__name__}: {exc}")
        return None

    package = load(config.package)
    if package is None:
        return _Found([], [], problems)
    names = [config.package] + [
        info.name
        for info in pkgutil.walk_packages(
            package.__path__, prefix=f"{config.package}.", onerror=lambda n: load(n)
        )
    ]
    for name in names:
        module = load(name)
        if module is not None:
            modules.append(module)

    # The modules must be this checkout's: a package already imported from elsewhere (the gate's
    # own copy, when the gate checks beethoven-qa in another checkout) is not what CrossHair reads.
    reached: set[Path] = set()
    for module in modules:
        file = getattr(module, "__file__", None)
        where = Path(file).resolve() if file else None
        if where is None or not where.is_relative_to(home):
            problems.append(f"{module.__name__} was imported from {where}, not from {home}")
        else:
            reached.add(where)
    # A .py file under the package that is not one of its modules (a directory without
    # __init__.py) is never imported, so neither check would see it.
    for path in sorted(home.rglob("*.py")):
        if "__pycache__" not in path.parts and path.resolve() not in reached:
            problems.append(
                f"{path.relative_to(root)} was not imported as a module of {config.package}"
            )
    return _Found(modules, names, problems)


def _home(obj: object, module: ModuleType, package: str) -> str:
    # The module a function counts in. Decided by identity, not by __module__ alone: a function
    # counts in its own module when that module binds it, publicly, under its own name; one from
    # outside the package is someone else's; anything else (a private function re-exported under
    # a public name, a wrapper whose __module__ names the decorator's) counts where it is bound.
    # A partial's own __module__ is functools' and it has no __name__, so the function it binds
    # says where it is from, and one over package code counts where it is bound.
    inner = _unwrap(obj)
    owner = getattr(inner if isinstance(obj, functools.partial) else obj, "__module__", None)
    owner = owner or getattr(inner, "__module__", None)
    if not isinstance(owner, str):
        return module.__name__
    if owner != package and not owner.startswith(package + "."):
        return owner
    name = getattr(obj, "__name__", None)
    home = sys.modules.get(owner)
    if isinstance(name, str) and is_public(name) and getattr(home, name, None) is obj:
        return owner
    return module.__name__


def _callables(module: ModuleType) -> list[tuple[str, object]]:
    return [
        (name, obj)
        for name, obj in vars(module).items()
        if callable(obj) and not inspect.isclass(obj) and not inspect.ismodule(obj)
    ]


def _entries(modules: list[ModuleType], package: str) -> tuple[FunctionEntry, ...]:
    # Coroutines are left out here and reported on their own (_async_public).
    return tuple(
        FunctionEntry(module.__name__, name, _home(obj, module, package), _has_contracts(obj))
        for module in modules
        for name, obj in _callables(module)
        if not _is_async(obj)
    )


def _async_public(modules: list[ModuleType], package: str, exempt: frozenset[str]) -> list[str]:
    found: list[str] = []
    for module in modules:
        if module.__name__ in exempt:
            continue
        for name, obj in _callables(module):
            if (
                is_public(name)
                and _is_async(obj)
                and _home(obj, module, package) == module.__name__
            ):
                found.append(f"{module.__name__}.{name}")
        for cname, cls in vars(module).items():
            if inspect.isclass(cls) and cls.__module__ == module.__name__:
                for mname, member in vars(cls).items():
                    if isinstance(member, (staticmethod, classmethod)):
                        member = member.__func__
                    if callable(member) and _is_async(member) and _has_contracts(member):
                        found.append(f"{module.__name__}.{cname}.{mname}")
    return sorted(found)


def _contracted_models(modules: list[ModuleType]) -> list[str]:
    # Measured 2026-10-04 with CrossHair 0.0.111: a pydantic model's contracted method was reported
    # "Not confirmed" and a broken one passed, because CrossHair never built an instance to call.
    return sorted(
        f"{module.__name__}.{name}"
        for module in modules
        for name, obj in vars(module).items()
        if inspect.isclass(obj)
        and obj.__module__ == module.__name__
        and issubclass(obj, BaseModel)
        and _class_has_contracts(obj)
    )


def _targets(modules: list[ModuleType], package: str) -> tuple[list[str], list[str], list[str]]:
    # What CrossHair proves, named one by one so the targets can be proven side by side: every
    # contracted function each module counts as its own, and every class it defines that carries
    # invariants or a contracted member; the two apart, since only a function can be set aside.
    # Classes with nothing to check are returned apart as well, to be named, never counted.
    functions: list[str] = []
    classes: list[str] = []
    bare: list[str] = []
    for module in modules:
        for name, obj in vars(module).items():
            qualified = f"{module.__name__}.{name}"
            if inspect.isclass(obj):
                if obj.__module__ != module.__name__:
                    continue
                (classes if _class_has_contracts(obj) else bare).append(qualified)
            elif (
                callable(obj)
                and not inspect.ismodule(obj)
                and _has_contracts(obj)
                and not _is_async(obj)
                and _home(obj, module, package) == module.__name__
            ):
                functions.append(qualified)
    return sorted(functions), sorted(classes), sorted(bare)


def _test_trees(root: Path) -> list[Path]:
    # Where pytest collects: the directories ``[tool.pytest.ini_options].testpaths`` names, else
    # ``tests``, and a ``conftest.py`` at the root, which pytest reads from anywhere below it.
    with (root / "pyproject.toml").open("rb") as fh:
        options = tomllib.load(fh).get("tool", {}).get("pytest", {}).get("ini_options", {})
    paths = options.get("testpaths", ["tests"])
    trees = [root / p for p in (paths.split() if isinstance(paths, str) else paths)]
    conftest = root / "conftest.py"
    return trees + ([conftest] if conftest.is_file() else [])


def _checked_files(
    root: Path, config: GateConfig, modules: list[ModuleType], only: str | None
) -> list[Path]:
    # The files read for what would hide a finding: under --module that module's own, and a stub
    # beside it; otherwise every .py and .pyi file of the package and of the directories pytest
    # collects, where a suppression or a skipped test most often sits. A pinned copy under
    # vendor/ is in neither, and vendor-check guards it.
    if only is not None:
        own = [Path(f) for m in modules if (f := getattr(m, "__file__", None))]
        return own + [stub for path in own if (stub := path.with_suffix(".pyi")).is_file()]
    trees = [(root / config.src / config.package).resolve(), *_test_trees(root)]
    files = [tree for tree in trees if tree.is_file()]
    return files + [
        path
        for tree in trees
        if tree.is_dir()
        for path in sorted(tree.rglob("*.py*"))
        if path.suffix in {".py", ".pyi"} and "__pycache__" not in path.parts
    ]


def _comments(path: Path) -> list[tuple[int, str]]:
    # Every comment of the file with its line, by tokenize, so a string holding the same text is
    # never one.
    with path.open("rb") as fh:
        return [
            (token.start[0], token.string)
            for token in tokenize.tokenize(fh.readline)
            if token.type == tokenize.COMMENT
        ]


def _dotted(node: ast.expr) -> str | None:
    # The dotted name an expression spells, or None when it is not a name.
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _bound_by(node: ast.Import | ast.ImportFrom) -> dict[str, str]:
    # What one import statement binds, to the dotted name each binding stands for: `import a.b`
    # binds `a` to the package `a`, `import a.b as c` binds `c` to `a.b`, and `from m import x as
    # y` binds `y` to `m.x`. A relative import reaches nothing of pytest's or unittest's and binds
    # nothing here.
    if isinstance(node, ast.ImportFrom):
        if not node.module or node.level:
            return {}
        return {alias.asname or alias.name: f"{node.module}.{alias.name}" for alias in node.names}
    bound: dict[str, str] = {}
    for alias in node.names:
        head = alias.name.partition(".")[0]
        bound[alias.asname or head] = alias.name if alias.asname else head
    return bound


def _imports(tree: ast.Module) -> dict[str, str]:
    # The name each import binds, to the dotted name it stands for, so `from pytest import mark` and
    # `import unittest as ut` are read as what they are.
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_bound_by(node))
    return names


def _skips(tree: ast.Module) -> list[tuple[int, str]]:
    # Every name that skips a test or expects it to fail, wherever it is written (a decorator, a
    # call, a mark bound to ``pytestmark`` or given to ``pytest.param`` or ``add_marker``, an
    # exception raised, a TestCase's ``skipTest``), by the dotted name as written and as the
    # module's imports resolve it. ast.walk meets a dotted name's outer node before its inner
    # ones, so a name is reported once, from where it begins.
    names = _imports(tree)
    found: dict[tuple[int, int], str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        if (node.lineno, node.col_offset) in found:
            continue
        dotted = _dotted(node)
        if dotted is None:
            continue
        head, dot, rest = dotted.partition(".")
        method = isinstance(node, ast.Attribute) and node.attr == _SKIP_METHOD
        if method or dotted in _SKIPS or names.get(head, head) + dot + rest in _SKIPS:
            found[node.lineno, node.col_offset] = dotted
    return [(line, name) for (line, _), name in found.items()]


def _hidden_in(path: Path) -> list[tuple[str, str]]:
    # (kind, "<line>: <what and why>") for each comment or test mark of one file that would hide
    # something; the file is read once by tokenize for its comments and once by ast for the names
    # a decorator or a call reaches, because a comment is no node and a string is no comment.
    found: list[tuple[str, str]] = []
    if path.suffix == ".pyi":
        return [("stub", f" {_NO_STUB}")]
    for line, text in _comments(path):
        if _DIRECTIVE.match(text):
            found.append(("directive", f"{line}: {text}"))
        elif _SUPPRESSION.search(text):
            found.append(("suppression", f"{line}: {text}: {_NO_SUPPRESSION}"))
    for line, name in _skips(ast.parse(path.read_bytes(), str(path))):
        found.append(("skip", f"{line}: {name}: {_NO_SKIP}"))
    return found


def _hidden(paths: list[Path], root: Path) -> list[str]:
    # Every line of ``paths`` that would hide something from the gate, the standards or the test
    # run, with its file and line, and once per kind the way out. A `# crosshair:` comment can
    # switch checking off or cut the search below the declared bound, and the command line cannot
    # override every one of them; a suppression comment is read by no tool the standards run, and
    # a skipped test runs nothing, and mypy reads a stub in place of the module beside it, so none
    # of them is allowed in a checked repository.
    found: list[str] = []
    for path in paths:
        rel = path.relative_to(root)
        try:
            found += [f"{kind} {rel}:{text}" for kind, text in _hidden_in(path)]
        except (tokenize.TokenError, SyntaxError) as exc:
            found.append(f"{rel}: cannot be read: {exc}")
    if any(line.startswith("directive ") for line in found):
        found.append(_INSTEAD_OF_A_DIRECTIVE)
    if any(line.startswith(("suppression ", "skip ", "stub ")) for line in found):
        found.append(_INSTEAD_OF_A_SUPPRESSION)
    return found


_DIRECTIVE = re.compile(r"#\s*crosshair\s*:", re.IGNORECASE)
# The comments that set a finding or a line aside for ruff (and flake8, and isort's markers that
# ruff's import rule honours, and the formatter's), mypy (every inline configuration comment,
# which loosens the file whatever it names), bandit, coverage.py, jscpd, zizmor or complexipy,
# each found wherever a hash begins it inside a comment, the way the tools find them:
# whitespace-tolerant, whatever the case.
_SUPPRESSION = re.compile(
    r"#\s*(?:"
    r"(?:(?:flake8|ruff)\s*:\s*)?noqa\b"
    r"|type\s*:\s*ignore\b"
    r"|mypy\s*:"
    r"|(?:ruff\s*:\s*)?isort\s*:\s*(?:skip|off)"
    r"|fmt\s*:\s*(?:off|skip)\b"
    r"|nosec\b"
    r"|pragma[:\s]\s*no\s*cover\b"
    r"|jscpd\s*:\s*ignore"
    r"|zizmor\s*:\s*ignore"
    r"|complexipy\s*:\s*ignore"
    r")"
    # lizard's two, read the way lizard reads them (its comment_counter), in this case only: a
    # comment that opens with the first, the hash joined to the word, drops the function it closes
    # from the count; the second anywhere in a comment drops the rest of the file.
    r"|(?-i:\A#lizard forgive)"
    r"|(?-i:GENERATED CODE)",
    re.IGNORECASE,
)
# The decorators and calls that keep a test from running or from counting.
_SKIPS = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "pytest.skip",
        "pytest.xfail",
        "pytest.importorskip",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
        "unittest.SkipTest",
        "unittest.case.skip",
        "unittest.case.skipIf",
        "unittest.case.skipUnless",
        "unittest.case.expectedFailure",
        "unittest.case.SkipTest",
        "pytest.skip.Exception",
        "_pytest.outcomes.skip",
        "_pytest.outcomes.xfail",
        "_pytest.outcomes.importorskip",
        "_pytest.outcomes.Skipped",
        "_pytest.outcomes.XFailed",
    }
)
# A TestCase's own way out, met as an attribute of whatever object, since ``self`` is no import.
_SKIP_METHOD = "skipTest"
_NO_SUPPRESSION = "no comment sets a finding aside; fix what it hides"
_NO_STUB = (
    "mypy reads a stub in place of the module beside it, whatever the module holds; no stub "
    "sets a finding aside, remove it and type the module"
)
_NO_SKIP = "a test is never skipped or expected to fail; make it pass or remove it"
_INSTEAD_OF_A_DIRECTIVE = (
    "instead of a directive: a function CrossHair must not or cannot run goes under "
    "[tool.beethoven-qa.unproven] with its reason; a bound below the declared one, never"
)
_INSTEAD_OF_A_SUPPRESSION = (
    "instead of a suppression: fix the finding the comment hides, narrow the type, or make the "
    "test pass; the standards honour no such comment and a test that does not run proves nothing"
)
_NOTHING_TO_CHECK = "WARNING: Targets found, but contain no checkable functions."
_VACUOUS = (
    "a precondition no input can meet: either the contract is vacuous, or it is stated over values "
    "CrossHair cannot build; restate it over an enum, or list the function under "
    "[tool.beethoven-qa.unproven] with that reason"
)


def _jobs() -> int:
    # The allocation's cores, not the node's: inside a Slurm job the affinity mask is what the job
    # was given. BEETHOVEN_QA_JOBS lowers it (a laptop running other work); it never raises it.
    cores = max(1, len(os.sched_getaffinity(0)))
    override = os.environ.get("BEETHOVEN_QA_JOBS", "")
    if override.isdigit() and int(override) >= 1:
        return min(int(override), cores)
    return cores


def _said(
    target: str, result: subprocess.CompletedProcess[str], timeout: int
) -> tuple[Verdicts, list[str]]:
    # What CrossHair said of one target, printed under its exit line with the count of its
    # verdicts, and what that output shows the gate must refuse.
    output = (result.stdout + result.stderr).strip()
    counts = verdicts(output)
    print(f"gate: {target}: exit {result.returncode}")
    print(
        f"gate: {target}: {counts.confirmed} confirmed over all paths, {counts.unconfirmed} "
        f"not confirmed within {timeout} s, {counts.unmet} unable to meet a precondition"
    )
    if output:
        print(output)
    problems: list[str] = []
    if result.returncode == 0 and _NOTHING_TO_CHECK in output:
        problems.append(f"CrossHair found nothing to check in {target}, which has contracts")
    if counts.unmet:
        problems.append(f"{target}: {_VACUOUS}")
    return counts, problems


def _report(
    targets: list[str], results: list[subprocess.CompletedProcess[str]], timeout: int
) -> tuple[int, Verdicts, list[str]]:
    # Every target's lines in order: the first non-zero exit, the counts over every target, and
    # what the outputs show the gate must refuse.
    failed = 0
    totals = Verdicts(0, 0, 0)
    problems: list[str] = []
    for target, result in zip(targets, results, strict=True):
        counts, found = _said(target, result, timeout)
        totals = Verdicts(*(sum(pair) for pair in zip(totals, counts, strict=True)))
        failed = failed or result.returncode
        problems.extend(found)
    return failed, totals, problems


def _prove(
    root: Path, config: GateConfig, targets: list[str], jobs: int
) -> list[subprocess.CompletedProcess[str]]:
    # Each condition may take up to its whole bound when its inputs are unbounded (strings, tuples),
    # so one CrossHair process over the package costs the sum of every bound; one process per
    # function or class, ``jobs`` at a time, costs about the largest.
    timeout = config.per_condition_timeout
    pythonpath = os.pathsep.join(
        p for p in (str(root / config.src), os.environ.get("PYTHONPATH", "")) if p
    )
    env = {**os.environ, "PYTHONPATH": pythonpath}

    def prove(target: str) -> subprocess.CompletedProcess[str]:
        cmd = crosshair_command(sys.executable, target, timeout)
        return subprocess.run(cmd, env=env, cwd=root, capture_output=True, text=True, check=False)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(prove, targets))


def _how_many(chosen: list[str], targets: list[str]) -> str:
    # How many targets are proven, out of how many when the change since a base chose fewer.
    if len(chosen) == len(targets):
        return str(len(targets))
    return f"{len(chosen)} of {len(targets)} (those the change reaches)"


# The gate's own files, beside this module wherever the copy the gate runs from sits: the package's
# start, the contract decorators, the gate, the narrowing and the runner CrossHair starts through.
_OWN_FILES = ("__init__.py", "contracts.py", "gate.py", "scope.py", "_crosshair.py")


def _imported_files() -> set[Path]:
    # The file of every module this process has imported, where the import found it, and the
    # compiled copy Python keeps of it, which an import may load in place of the source.
    modules = list(sys.modules.values())
    files = (getattr(m, name, None) for m in modules for name in ("__file__", "__cached__"))
    return {Path(file).resolve() for file in files if isinstance(file, str)}


def _everywhere(root: Path, config: GateConfig) -> frozenset[str]:
    # The files of the repository every proof reads: the root files, the gate's own (inside the
    # package when the gate checks itself), and any other the gate's process imported from outside
    # the package (the rest of a pinned copy, a module the package imports from beside it). A
    # module of the environment sits in no file git tracks, so it never matches a change.
    home = (root / config.src / config.package).resolve()
    own = {Path(__file__).resolve().parent / name for name in _OWN_FILES}
    beside = {path for path in _imported_files() if not path.is_relative_to(home)}
    inside = {
        path.relative_to(root).as_posix() for path in own | beside if path.is_relative_to(root)
    }
    return ROOT_FILES | inside


def _changes(root: Path, base: str) -> tuple[str, tuple[str, ...]]:
    # The base commit and every file changed since it; ValueError, OSError or git's own error when
    # it cannot be told, as from a gate root that is not the repository's top level, whose paths
    # git would give from elsewhere.
    if _toplevel(root) != root:
        raise ValueError(f"{root} is not the top level of a git repository")
    commit = _resolve(root, base)
    if commit is None:
        raise ValueError("no base")
    return commit, _changed(root, commit)


class _Reach(NamedTuple):
    # What check 2 proves with a base: the modules (None for every one), the targets beyond them
    # that the unproven entries a change removed name, and the line that says why.
    modules: frozenset[str] | None
    named: frozenset[str]
    why: str


def _linked(root: Path, config: GateConfig) -> str | None:
    # The first file every proof reads, or entry of the package's directory, that the checkout
    # reaches through a link (the entry itself, or a folder on its way), or None: git names an edit
    # to it by the link's target, which no rule of beethoven_qa.scope counts.
    top = root.resolve()
    home = root / config.src / config.package
    paths = [root / name for name in sorted(_everywhere(root, config))] + [home]
    for path in paths + sorted(home.rglob("*")):
        if path.is_symlink() or (path.exists() and path.resolve() != top / path.relative_to(root)):
            return path.relative_to(root).as_posix()
    return None


def _wide(root: Path, config: GateConfig, commit: str, changed: tuple[str, ...]) -> str:
    # Why every module is proven before the table file is read, or nothing: a link among what the
    # rules read by path, a link or a submodule git tracks anywhere, or a changed file whose bytes
    # every proof reads (the table file is read by its keys instead, scope.table_reach).
    linked = _linked(root, config)
    if linked:
        return f"{linked} is reached through a link, so git names an edit to it by another path"
    tracked = _tracked_link(root, commit)
    if tracked:
        return tracked
    where = (config.src, config.package)
    everywhere = _everywhere(root, config) - {TABLE_FILE}
    path = next((path for path in changed if reaches_every_module(path, *where, everywhere)), None)
    return f"{path} changed since {commit[:12]}, and every proof reads it" if path else ""


def _decided(root: Path, config: GateConfig, base: str) -> _Reach:
    # The rules of beethoven_qa.scope in order: a link or a file read by its bytes, the table file
    # by its keys, then the modules the change reaches, with the targets removed unproven entries
    # name.
    commit, changed = _changes(root, base)
    why = _wide(root, config, commit, changed)
    named: frozenset[str] = frozenset()
    if not why and TABLE_FILE in changed:
        every, named = _table_change(root, commit)
        why = f"{every}, and every proof reads it" if every else ""
    if why:
        return _Reach(None, frozenset(), f"every module: {why}")
    where = (config.src, config.package)
    graph = _graph(root, *where)
    touched = frozenset(m for path in changed if (m := module_named(path, *where)) is not None)
    modules = reached(touched, graph.imports, graph.dynamic)
    counted = f"{len(modules)} of {len(graph.imports)} modules"
    why = f"{counted}, those the change since {commit[:12]} reaches: {_listed(modules)}"
    if named:
        why += f"; and {_listed(named)}, which the unproven entries it removed named"
    return _Reach(modules, named, why)


def _reach(root: Path, config: GateConfig, base: str) -> _Reach:
    # What check 2 proves; with no base, every module and nothing to say, as the gate ran before
    # it took one (beethoven_qa.scope). What cannot be read proves every module, never fails.
    if base == "none":
        return _Reach(None, frozenset(), "")
    try:
        return _decided(root, config, base)
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        # RuntimeError: pathlib's resolve on a loop of links.
        why = f"every module, as what changed since {base} cannot be told: {exc}"
        return _Reach(None, frozenset(), why)


def _listed(names: frozenset[str]) -> str:
    return ", ".join(sorted(names)) or "none"


def _chosen(root: Path, config: GateConfig, targets: list[str], base: str) -> list[str]:
    # The targets check 2 proves: every one, or with a base only those of the modules the change
    # since it reaches, a target being named by the module that counts it, and those the unproven
    # entries it removed name.
    reach = _reach(root, config, base)
    if reach.why:
        print(f"gate: proof: {reach.why}")
    if reach.modules is None:
        return targets
    return [
        target
        for target in targets
        if target.rpartition(".")[0] in reach.modules or target in reach.named
    ]


def _run(root: Path, only: str | None = None, base: str = "none") -> int:
    # Both checks for the repository at ``root``, or for its module ``only``; 0 when both pass, 2
    # when ``only`` is not a checked module of the package. With a ``base`` other than none, check
    # 2 proves only the modules the change since it reaches (beethoven_qa.scope); the command line
    # gives none under --module. Private and uncontracted on purpose: it imports the package and
    # starts CrossHair, which CrossHair must never do itself.
    config = GateConfig.from_pyproject(root)
    exempt = frozenset(config.exempt)
    found = _modules(config, root)
    modules = found.modules
    problems = list(found.problems)
    if any(" was imported from " in p for p in problems):
        # CrossHair would read this checkout while the checks above read another: nothing to run.
        for line in problems:
            print(f"gate: {line}")
        return 1
    if only is not None:
        if only in exempt or only not in {m.__name__ for m in modules}:
            print(f"gate: {only} is not a checked module of {config.package}")
            for line in problems:
                if line.startswith(f"importing {only} "):
                    print(f"gate: {line}")  # why it is not: the import itself failed
            return 2
        modules = [m for m in modules if m.__name__ == only]
        problems = [p for p in problems if p.startswith(f"importing {only} ")]
    problems += _hidden(_checked_files(root, config, modules, only), root)
    problems += [
        f"{name} is a coroutine: CrossHair would check the un-awaited coroutine object; keep the "
        "logic in a synchronous contracted function and the coroutine private"
        for name in _async_public(modules, config.package, exempt)
    ]
    problems += [
        f"{name} is a pydantic model with contracts: CrossHair does not construct one, so they "
        "would never be exercised; keep the model a boundary and the logic in contracted functions"
        for name in _contracted_models(modules)
    ]
    missing = uncontracted(_entries(modules, config.package), exempt)
    if missing:
        problems.append("public functions without contracts (nothing to prove):")
        problems += [f"  - {name}" for name in missing]

    functions, classes, bare = _targets(modules, config.package)
    scope = only or config.package
    # Under --module, every entry under the module is that module's and is reported there, never
    # dropped (a method entry, pkg.mod.Class.m, included); only an entry under a deeper module of
    # the package (pkg.mod.sub.f, when the module is a package) belongs elsewhere. Deeper means
    # every module the walk found, imported or not: an entry for one that failed to import would
    # otherwise be matched against this module's functions and called stale, with the failure,
    # the real cause, printed only under that module's own run.
    deeper = tuple(f"{n}." for n in found.names if only and n.startswith(f"{only}."))
    listed = {
        name: why
        for name, why in config.unproven.items()
        if only is None or (name.startswith(f"{only}.") and not name.startswith(deeper))
    }
    proven, misnamed, stale = set_aside(tuple(functions), tuple(classes + bare), tuple(listed))
    targets = sorted([*proven, *classes])
    problems += [
        f"unproven entry {name} names a class of {scope} or a method of one: a class is proven "
        "whole, and I/O stays out of a contracted class; remove the entry"
        for name in misnamed
    ]
    problems += [
        f"unproven entry {name} names no contracted function of {scope}: remove the entry"
        for name in stale
    ]
    if not targets:
        problems.append(f"nothing to prove in {scope}: no contracted function or class")
    chosen = _chosen(root, config, targets, base)
    jobs = _jobs()
    timeout = config.per_condition_timeout
    print(
        f"gate: CrossHair + Z3 over {scope}: {_how_many(chosen, targets)} contracted functions "
        f"and classes, {jobs} at a time, {timeout} s per condition"
    )
    if bare:
        print(f"gate: classes with no contracts, nothing to check: {', '.join(bare)}")
    for name in sorted(set(listed) - set(misnamed) - set(stale)):
        print(f"gate: not proven, contracts checked at run time only: {name}: {listed[name]}")
    results = _prove(root, config, chosen, jobs)
    failed, totals, refused = _report(chosen, results, timeout)
    problems += refused
    for line in problems:
        print(line if line.startswith("  - ") else f"gate: {line}")
    # The last line before the verdict, in this exact form: the report command reads it back.
    print(
        f"gate: conditions: {totals.confirmed} of {sum(totals)} confirmed over all paths, "
        f"{totals.unconfirmed} not confirmed within {timeout} s per condition, "
        f"{totals.unmet} unable to meet a precondition"
    )
    if failed or problems:
        return failed or 1
    print(f"gate: no counterexample within {timeout} s per condition")
    return 0


if __name__ == "__main__":
    # `python -m beethoven_qa.gate` would otherwise import this module, check nothing and exit 0.
    raise SystemExit("run the gate as: python -m beethoven_qa gate")
