#!/usr/bin/env python3
"""The symbolic verification gate (definition of done, step 4).

Two checks, in order, and the gate fails on either:

1. Every public function in the package carries icontract contracts. A module with nothing to prove
   is reported, not silently passed: a gate that only ever says "no counterexample" because there
   was nothing to check is the wrong-but-plausible failure this project guards against.
2. CrossHair + Z3 find no input that satisfies the preconditions and violates a postcondition,
   within the per-condition timeout. CrossHair exits 1 on a counterexample and prints it; that is
   the SpecPylot feedback a future self-healing loop reads back.

Run from the repository root: ``python tools/verify_gate.py`` (``pixi run symbolic``). The package
is imported from src/, so PYTHONPATH must include it (pixi's activation does this).
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

PACKAGE = "beethoven_research"
SRC = Path(__file__).resolve().parent.parent / "src"
# Seconds Z3 may spend per condition. Linear arithmetic decides in milliseconds; the budget is for
# the day a module brings something harder, so a slow proof reads as "not proven in N s", never
# as a pass.
PER_CONDITION_TIMEOUT = int(os.environ.get("VERIFY_GATE_TIMEOUT", "20"))
# Modules that are allowed to carry no contracts: wiring and re-exports, never logic.
EXEMPT_MODULES = {f"{PACKAGE}", f"{PACKAGE}.contracts"}


def _has_contracts(fn: object) -> bool:
    # icontract records its contracts as attributes on the function it returns. beartype wraps
    # outside it (contracts.py) and carries those attributes through to its own wrapper, so the
    # outermost object is the one to inspect; measured, not assumed, see contracts.py.
    return getattr(fn, "__preconditions__", None) is not None or hasattr(fn, "__postconditions__")


def _uncontracted_public_functions() -> list[str]:
    sys.path.insert(0, str(SRC))
    package = importlib.import_module(PACKAGE)
    missing: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{PACKAGE}."):
        if info.name in EXEMPT_MODULES:
            continue
        module = importlib.import_module(info.name)
        for name, obj in vars(module).items():
            if name.startswith("_") or not inspect.isfunction(obj):
                continue
            if obj.__module__ != info.name:
                continue  # imported from elsewhere; its own module is checked
            if not _has_contracts(obj):
                missing.append(f"{info.name}.{name}")
    return missing


def main() -> int:
    missing = _uncontracted_public_functions()
    if missing:
        print("verify_gate: public functions without contracts (nothing to prove):")
        for name in missing:
            print(f"  - {name}")
        return 1

    cmd = [
        sys.executable,
        "-m",
        "crosshair",
        "check",
        str(SRC / PACKAGE),
        "--analysis_kind=icontract",
        f"--per_condition_timeout={PER_CONDITION_TIMEOUT}",
    ]
    print("verify_gate:", " ".join(cmd[1:]))
    pythonpath = os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")])
    result = subprocess.run(cmd, env={**os.environ, "PYTHONPATH": pythonpath}, check=False)
    if result.returncode == 0:
        print(f"verify_gate: no counterexample within {PER_CONDITION_TIMEOUT} s per condition")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
