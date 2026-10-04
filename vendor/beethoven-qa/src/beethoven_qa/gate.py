"""The symbolic verification gate (definition of done, step 4), for any repository that carries this
package.

Two checks; both always run, and the gate fails on either:

1. Every public function of the repository's package carries icontract contracts, and nothing
   keeps a function out of CrossHair's sight: every ``.py`` file under the package is imported as
   one of its modules (and imports without exiting), no ``# crosshair:`` directive turns checking
   off or cuts it short, and no public coroutine waits to be proven (CrossHair would check the
   un-awaited coroutine object). A package with nothing to prove is a failure, not a pass: a gate
   that only ever says "no counterexample" because there was nothing to check is the
   wrong-but-plausible failure this project guards against.
2. CrossHair + Z3 find no input that satisfies the preconditions and violates a postcondition,
   within the per-condition timeout, for every contracted function and every class that carries
   contracts. CrossHair exits 1 on a counterexample and prints it; that is the SpecPylot feedback
   the ``verify-module`` workflow reads back. With ``--report-all`` every condition's verdict is
   printed, and a precondition no input can meet (a vacuous contract) fails the gate too.

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
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from beethoven_qa.contracts import ensure, require

__all__ = [
    "FunctionEntry",
    "GateConfig",
    "crosshair_command",
    "is_dotted_identifier",
    "is_public",
    "set_aside",
    "uncontracted",
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
@ensure(lambda result: "--analysis_kind=icontract" in result, "the icontract contracts are checked")
@ensure(
    lambda result, timeout: f"--per_condition_timeout={timeout}" in result,
    "the declared bound is the one applied",
)
@ensure(lambda result, target: target in result, "the target is the one checked")
@ensure(
    lambda result, report_all: (result[-1] == "--report_all") == report_all,
    "every verdict is printed exactly when it was asked for",
)
def crosshair_command(
    python: str, target: str, timeout: int, report_all: bool = False
) -> list[str]:
    """The CrossHair command line that proves the contracts of ``target`` (a function, a class, a
    module or a directory) within ``timeout`` seconds per condition."""
    return [
        python,
        "-m",
        "crosshair",
        "check",
        target,
        "--analysis_kind=icontract",
        f"--per_condition_timeout={timeout}",
        *(["--report_all"] if report_all else []),
    ]


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


def _directives(paths: list[Path], root: Path) -> list[str]:
    # A `# crosshair:` comment can switch checking off or cut the search below the declared bound,
    # and the command line cannot override every one of them; none is allowed in a checked package.
    found: list[str] = []
    for path in paths:
        with path.open("rb") as fh:
            try:
                for token in tokenize.tokenize(fh.readline):
                    if token.type == tokenize.COMMENT and _DIRECTIVE.match(token.string):
                        found.append(f"{path.relative_to(root)}:{token.start[0]}: {token.string}")
            except (tokenize.TokenError, SyntaxError) as exc:
                found.append(f"{path.relative_to(root)}: cannot be read: {exc}")
    return found


_DIRECTIVE = re.compile(r"#\s*crosshair\s*:", re.IGNORECASE)
_NOTHING_TO_CHECK = "WARNING: Targets found, but contain no checkable functions."
_VACUOUS = "Unable to meet precondition."


def _jobs() -> int:
    # The allocation's cores, not the node's: inside a Slurm job the affinity mask is what the job
    # was given. BEETHOVEN_QA_JOBS lowers it (a laptop running other work); it never raises it.
    cores = max(1, len(os.sched_getaffinity(0)))
    override = os.environ.get("BEETHOVEN_QA_JOBS", "")
    if override.isdigit() and int(override) >= 1:
        return min(int(override), cores)
    return cores


def _run(root: Path, only: str | None = None, report_all: bool = False) -> int:
    # Both checks for the repository at ``root``, or for its module ``only``; 0 when both pass, 2
    # when ``only`` is not a checked module of the package. Private and uncontracted on purpose: it
    # imports the package and starts CrossHair, which CrossHair must never do itself.
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
    files = [Path(f) for m in modules if (f := getattr(m, "__file__", None))]
    if only is None:
        home = (root / config.src / config.package).resolve()
        files = [p for p in sorted(home.rglob("*.py")) if "__pycache__" not in p.parts]
    directives = _directives(files, root)
    problems += [f"directive {d}" for d in directives]
    if directives:
        problems.append(
            "instead of a directive: a function CrossHair must not or cannot run goes under "
            "[tool.beethoven-qa.unproven] with its reason; a bound below the declared one, never"
        )
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

    # Each condition may take up to its whole bound when its inputs are unbounded (strings, tuples),
    # so one CrossHair process over the package costs the sum of every bound; one process per
    # function or class, as many at a time as there are cores, costs about the largest.
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
    jobs = _jobs()
    timeout = config.per_condition_timeout
    print(
        f"gate: CrossHair + Z3 over {scope}: {len(targets)} contracted functions and classes, "
        f"{jobs} at a time, {timeout} s per condition"
    )
    if bare:
        print(f"gate: classes with no contracts, nothing to check: {', '.join(bare)}")
    for name in sorted(set(listed) - set(misnamed) - set(stale)):
        print(f"gate: not proven, contracts checked at run time only: {name}: {listed[name]}")
    pythonpath = os.pathsep.join(
        p for p in (str(root / config.src), os.environ.get("PYTHONPATH", "")) if p
    )
    env = {**os.environ, "PYTHONPATH": pythonpath}

    def prove(target: str) -> subprocess.CompletedProcess[str]:
        cmd = crosshair_command(sys.executable, target, timeout, report_all)
        return subprocess.run(cmd, env=env, cwd=root, capture_output=True, text=True, check=False)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(prove, targets))
    failed = 0
    for target, result in zip(targets, results, strict=True):
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or output:
            print(f"gate: {target}: exit {result.returncode}")
            if output:
                print(output)
        if result.returncode != 0:
            failed = failed or result.returncode
        elif _NOTHING_TO_CHECK in output:
            problems.append(f"CrossHair found nothing to check in {target}, which has contracts")
        elif _VACUOUS in output:
            problems.append(f"{target}: a precondition no input can meet (a vacuous contract)")
    for line in problems:
        print(line if line.startswith("  - ") else f"gate: {line}")
    if failed or problems:
        return failed or 1
    print(f"gate: no counterexample within {timeout} s per condition")
    return 0


if __name__ == "__main__":
    # `python -m beethoven_qa.gate` would otherwise import this module, check nothing and exit 0.
    raise SystemExit("run the gate as: python -m beethoven_qa gate")
