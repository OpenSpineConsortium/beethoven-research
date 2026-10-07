"""Design: abstraction, encapsulation and coupling of classes, as the Chidamber and Kemerer suite
measures them (IEEE Transactions on Software Engineering, 1994), with LCOM4 for cohesion.

No maintained tool computes the suite for Python (the Drive file's example, Aniche's ``ck``, reads
Java), so it is computed here from the syntax tree, each metric to a stated definition, with the
McCabe weights of WMC taken from radon. Six limits on every class of the package:

- **WMC at most 50**: the sum of the McCabe complexity of the class's own methods (radon's values;
  a method of a class nested in another, which radon does not list, weighs 1, the suite's
  unweighted form). The limit is chosen here: the literature gives none that holds across
  languages, and the Drive file warns that WMC tracks size.
- **DIT at most 5** (the Drive file: "values greater than 5" are flagged): the longest path to
  ``object``. A class with no base has 1; a base defined outside the package counts as one level,
  its own ancestry not read; a base defined in the package adds its own depth.
- **NOC at most 10**: classes of the package naming the class as a base. Chosen here.
- **CBO at most 14** (Sahraoui, Boukadoum and Lounis, 2000): the other classes of the package this
  class names, or that name it, in either direction, as CK define coupling. Library classes are
  not counted, as CK leave out the standard library.
- **RFC at most 50**: the class's own methods and every distinct name its methods call. Chosen
  here.
- **LCOM4 at most 1** (Hitz and Montazeri, 1995): the instance methods (dunder methods and static
  and class methods left out, since they do not share instance state or touch all of it) form a
  graph, joined when two use the same attribute of ``self`` or one calls the other; more than one
  connected part is a class that can be split.

A NamedTuple or a pydantic model with no methods measures 0 or 1 on every count and passes.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import NamedTuple

from beartype import beartype

from beethoven_qa import __version__
from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import Finding, Measure, blocking, over_limit
from beethoven_qa.standards.group import (
    Context,
    Group,
    Report,
    _bin,
    _compare,
    _definitions,
    _python_files,
    _read,
    _tool,
)
from beethoven_qa.standards.sarif import Rule, Run

__all__ = ["GROUP", "LIMITS", "RULES", "coupling", "inheritance_depth", "lcom4"]

GROUP = Group(
    "design",
    "abstraction, encapsulation and coupling of classes",
    "the Chidamber and Kemerer metrics suite, with LCOM4",
)

_HELP = "https://www.aivosto.com/project/help/pm-oo-ck.html"
_METRICS = (
    ("wmc", "WMC", "weighted methods (WMC)", 50, "Weighted Methods per Class; limit chosen here."),
    ("dit", "DIT", "depth of inheritance (DIT)", 5, "Depth of Inheritance Tree; the Drive file."),
    ("noc", "NOC", "number of children (NOC)", 10, "Number of Children; limit chosen here."),
    ("cbo", "CBO", "coupling (CBO)", 14, "Coupling Between Objects; Sahraoui et al., 2000."),
    ("rfc", "RFC", "response set (RFC)", 50, "Response For a Class; limit chosen here."),
    ("lcom4", "LCOM4", "lack of cohesion (LCOM4)", 1, "LCOM4; Hitz and Montazeri, 1995."),
)
LIMITS: dict[str, int] = {f"design/{key}": limit for key, _, _, limit, _ in _METRICS}
_WHAT = {f"design/{key}": what for key, _, what, _, _ in _METRICS}
RULES: dict[str, Rule] = {
    f"design/{key}": Rule(
        f"design/{key}",
        name,
        f"A class's {what} is at most {limit}.",
        f"Chidamber and Kemerer (1994): {source}",
        _HELP,
        ("maintainability", "design"),
    )
    for key, name, what, limit, source in _METRICS
}

# (a method's name, the attributes of self it uses, the methods of self it calls)
Method = tuple[str, frozenset[str], frozenset[str]]


def _components(methods: tuple[Method, ...]) -> int:
    parent = {m[0]: m[0] for m in methods}

    def root(name: str) -> str:
        while parent[name] != name:
            name = parent[name]
        return name

    owner: dict[str, str] = {}
    for name, used, called in methods:
        for attribute in sorted(used):
            parent[root(name)] = root(owner.setdefault(attribute, name))
        for other in sorted(called & parent.keys()):
            parent[root(name)] = root(other)
    return len({root(m[0]) for m in methods})


def _common(methods: tuple[Method, ...]) -> frozenset[str]:
    # The attributes every method uses. With ``&`` and not ``frozenset.intersection``: CrossHair
    # 0.0.111's symbolic frozenset refuses the unbound method (measured 2026-10-05).
    common = methods[0][1] if methods else frozenset()
    for _, used, _ in methods[1:]:
        common = common & used
    return common


@beartype
@require(lambda methods: len({m[0] for m in methods}) == len(methods), "each method once")
@ensure(lambda result, methods: (result == 0) == (len(methods) == 0), "no methods, no parts")
@ensure(lambda result, methods: 0 <= result <= len(methods), "at most one part per method")
@ensure(
    lambda result, methods: len(methods) == 0 or not _common(methods) or result == 1,
    "methods that all use one attribute are one cohesive class",
)
@ensure(
    lambda result, methods: (
        result == len(methods)
        or any(
            m[1] & n[1] or n[0] in m[2] or m[0] in n[2] for m in methods for n in methods if m != n
        )
    ),
    "two methods are joined only through an attribute they share or a call",
)
def lcom4(methods: tuple[Method, ...]) -> int:
    """LCOM4: the number of connected parts among a class's instance methods, two methods being
    joined when they use the same attribute of ``self`` or one calls the other."""
    return _components(methods)


def _depth(name: str, table: dict[str, tuple[str, ...]], seen: frozenset[str]) -> int:
    deepest = 0
    for base in table.get(name, ()):
        if base in table and base not in seen:
            deepest = max(deepest, _depth(base, table, seen | {base}))
        else:
            deepest = max(deepest, 1)  # outside the package (or a name cycle): one level
    return deepest + 1


@beartype
@require(lambda bases: len({c for c, _ in bases}) == len(bases), "each class once")
@require(lambda name, bases: name in dict(bases), "a class of the package")
@ensure(lambda result: result >= 1, "every class is at least one level below object")
@ensure(lambda result, bases: result <= len(bases) + 1, "no deeper than the classes allow")
@ensure(
    lambda result, name, bases: bool(dict(bases)[name]) or result == 1,
    "a class with no base is one level below object",
)
@ensure(
    lambda result, name, bases: not dict(bases)[name] or result >= 2,
    "a class with a base is at least two levels below object",
)
def inheritance_depth(name: str, bases: tuple[tuple[str, tuple[str, ...]], ...]) -> int:
    """DIT: the longest path from the class ``name`` to ``object``, given each class of the package
    with the names of its bases; a base outside the package counts as one level."""
    return _depth(name, dict(bases), frozenset({name}))


@beartype
@require(lambda references: len({c for c, _ in references}) == len(references), "each class once")
@require(lambda name, references: name in dict(references), "a class of the package")
@ensure(lambda result, references: 0 <= result < len(references), "never itself")
@ensure(
    lambda result, name, references: (
        result
        == len(
            {
                c
                for c, names in references
                if c != name and (c in dict(references)[name] or name in names)
            }
        )
    ),
    "the other classes it names or that name it, each counted once",
)
def coupling(name: str, references: tuple[tuple[str, frozenset[str]], ...]) -> int:
    """CBO: how many other classes of the package the class ``name`` is coupled to, given each class
    with the names its body uses; coupling runs both ways, as CK define it."""
    mine = dict(references)[name]
    return sum(1 for c, names in references if c != name and (c in mine or name in names))


class _Class(NamedTuple):
    path: str
    name: str  # qualified, e.g. "Outer.Inner"
    line: int
    end_line: int
    bases: tuple[str, ...]  # simple names, as written
    methods: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    names: frozenset[str]  # every name and attribute its body uses


def _simple(node: ast.expr) -> str | None:
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _used(node: ast.AST) -> frozenset[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return frozenset(names)


def _class(rel: str, node: ast.ClassDef, defs: dict[int, tuple[str, int]]) -> _Class:
    bases = tuple(b for e in node.bases if (b := _simple(e)) and b != "object")
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
    methods = tuple(s for s in node.body if isinstance(s, kinds))
    name, end = defs.get(node.lineno, (node.name, node.lineno))
    return _Class(rel, name, node.lineno, end, bases, methods, _used(node))


def _classes(tree: Path, files: list[str]) -> list[_Class]:
    found = []
    for rel in files:
        source = _read(tree, rel)
        try:
            module = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        defs = _definitions(source)
        nodes = (n for n in ast.walk(module) if isinstance(n, ast.ClassDef))
        found += [_class(rel, node, defs) for node in nodes]
    return found


def _instance(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    # The name the method calls its instance by, or None for a static or class method, a method
    # without parameters, and a dunder method.
    decorators = {_simple(d) for d in method.decorator_list}
    args = method.args.posonlyargs + method.args.args
    dunder = method.name.startswith("__") and method.name.endswith("__")
    if dunder or not args or decorators & {"staticmethod", "classmethod"}:
        return None
    return args[0].arg


def _cohesion_input(cls: _Class) -> tuple[Method, ...]:
    own = {m.name for m in cls.methods}
    graph = []
    for method in cls.methods:
        me = _instance(method)
        if me is None:
            continue
        on_self = {
            n.attr
            for n in ast.walk(method)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == me
        }
        graph.append((method.name, frozenset(on_self - own), frozenset(on_self & own)))
    unique = {name: (name, used, called) for name, used, called in graph}
    return tuple(unique.values())


def _response(cls: _Class) -> int:
    called = {
        name
        for method in cls.methods
        for n in ast.walk(method)
        if isinstance(n, ast.Call) and (name := _simple(n.func)) is not None
    }
    return len({m.name for m in cls.methods} | called)


def _weights(tree: Path, files: list[str]) -> dict[tuple[str, int], int]:
    # McCabe complexity per method, by file and the line of its def, as radon computes it.
    if not files:
        return {}
    out = _tool([_bin("radon"), "cc", "--json", *(str(tree / f) for f in files)])
    if out.returncode != 0:
        raise RuntimeError(f"radon failed: {out.stderr.strip()}")
    weights = {}
    for path, blocks in json.loads(out.stdout or "{}").items():
        rel = Path(path).resolve().relative_to(tree.resolve()).as_posix()
        for block in blocks if isinstance(blocks, list) else []:
            if block.get("type") == "method":
                weights[(rel, int(block["lineno"]))] = int(block["complexity"])
    return weights


def _measures(tree: Path, files: list[str]) -> list[Measure]:
    classes = _classes(tree, files)
    weights = _weights(tree, files)
    simple = {c.name.rsplit(".", 1)[-1] for c in classes}
    bases = {c.name.rsplit(".", 1)[-1]: tuple(b for b in c.bases) for c in classes}
    names = {c.name.rsplit(".", 1)[-1]: c.names & simple for c in classes}
    found = []
    for cls in classes:
        me = cls.name.rsplit(".", 1)[-1]
        values = {
            "wmc": sum(weights.get((cls.path, m.lineno), 1) for m in cls.methods),
            "dit": inheritance_depth(me, tuple(bases.items())),
            "noc": sum(1 for c in classes if me in c.bases and c is not cls),
            "cbo": coupling(me, tuple(names.items())),
            "rfc": _response(cls),
            "lcom4": lcom4(_cohesion_input(cls)),
        }
        span = (cls.path, cls.name, cls.line, cls.end_line)
        found += [Measure(f"design/{key}", *span, value) for key, value in values.items()]
    return found


def _findings(tree: Path, package: str) -> tuple[Finding, ...]:
    measures = _measures(tree, _python_files(tree, package))
    return tuple(
        f
        for m in measures
        if (f := over_limit(m, LIMITS[m.rule], _WHAT[m.rule], RULES[m.rule].tags)) is not None
    )


def _check(context: Context) -> Report:
    head, states = _compare(context, lambda tree: _findings(tree, context.package))
    rules = tuple(RULES.values())
    run = Run(GROUP.name, "beethoven-qa", __version__, _HELP, rules, head, states)
    stops = blocking(head, states)
    summary = f"{len(head)} class measures over a limit in the tree, {len(stops)} new or worse"
    return Report(GROUP.name, (run,), stops, not stops, summary)
