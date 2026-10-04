#!/usr/bin/env python3
"""The symbolic verification gate (definition of done, step 4).

Two checks, in order, and the gate fails on either:

1. Every public function in the package carries icontract contracts. A module with nothing to prove
   is reported, not silently passed: a gate that only ever says "no counterexample" because there
   was nothing to check is the wrong-but-plausible failure this project guards against.
2. CrossHair + Z3 find no input that satisfies the preconditions and violates a postcondition,
   within the per-condition timeout. CrossHair exits 1 on a counterexample and prints it; that is
   the SpecPylot feedback a future self-healing loop reads back. Each module is checked in a
   process of its own, a few at a time (see ``JOBS``).

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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PACKAGE = "beethoven_research"
SRC = Path(__file__).resolve().parent.parent / "src"
# Seconds Z3 may spend per condition. Linear arithmetic decides in milliseconds; the budget is for
# the day a module brings something harder, so a slow proof reads as "not proven in N s", never
# as a pass.
PER_CONDITION_TIMEOUT = int(os.environ.get("VERIFY_GATE_TIMEOUT", "20"))
# Modules that are allowed to carry no contracts: wiring and re-exports, never logic.
EXEMPT_MODULES = {f"{PACKAGE}", f"{PACKAGE}.contracts"}
# One CrossHair process per module, this many at once. In a single process one analysis can break
# the next: on Python 3.11 icontract's in-progress guard and CrossHair's lazy values leak between
# analyses (CrossHair's own suite skips test_icontract_class for it), and the whole-package run of
# 2026-10-04 reported a RecursionError in compile_rules and render_state, which each check clean
# alone and return at once on the reported input. Separate processes also cut the wall time.
JOBS = max(1, int(os.environ.get("VERIFY_GATE_JOBS", str(os.cpu_count() or 1))))


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

    # Largest first, so the long proofs start at once and the small ones fill in around them.
    files = sorted((SRC / PACKAGE).rglob("*.py"), key=lambda f: (-f.stat().st_size, f))
    pythonpath = os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")])
    env = {**os.environ, "PYTHONPATH": pythonpath}
    print(
        f"verify_gate: crosshair check, {len(files)} modules, {JOBS} at a time,"
        f" --analysis_kind=icontract --per_condition_timeout={PER_CONDITION_TIMEOUT}",
        flush=True,
    )
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        runs = {pool.submit(_check, path, env): path for path in files}
        for done in as_completed(runs):
            path, code, output, seconds = done.result()
            name = path.relative_to(SRC).as_posix()
            verdict = "clean" if code == 0 else f"exit {code}"
            if code == 0 and "contain no checkable functions" in output:
                verdict, output = "nothing CrossHair can check", ""  # wiring, or I/O marked off
            print(f"verify_gate: {name}: {verdict} in {seconds:.0f} s", flush=True)
            if output.strip():
                print(output.rstrip(), flush=True)
            if code != 0:
                failed.append(name)
    if failed:
        print(f"verify_gate: not proven: {', '.join(sorted(failed))}")
        return 1
    print(f"verify_gate: no counterexample within {PER_CONDITION_TIMEOUT} s per condition")
    return 0


def _check(path: Path, env: dict[str, str]) -> tuple[Path, int, str, float]:
    cmd = [
        sys.executable,
        "-m",
        "crosshair",
        "check",
        str(path),
        "--analysis_kind=icontract",
        f"--per_condition_timeout={PER_CONDITION_TIMEOUT}",
    ]
    start = time.monotonic()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    return path, proc.returncode, proc.stdout + proc.stderr, time.monotonic() - start


if __name__ == "__main__":
    raise SystemExit(main())
