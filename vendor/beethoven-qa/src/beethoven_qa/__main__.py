"""The command line: ``python -m beethoven_qa gate | standards | coach | vendor | vendor-check |
boundary | report``.

Wiring and I/O only, so the gate exempts this module from the contract rule (pyproject.toml); the
logic it calls lives in contracted, symbolically checked functions of gate.py, vendor.py,
boundary.py, report.py and the standards groups. What a consumer may type is interface/cli.json,
the cli export; tests/test_interface.py holds this parser to it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from beethoven_qa import __version__, boundary, gate, report, vendor
from beethoven_qa.standards import coach
from beethoven_qa.standards import run as standards


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m beethoven_qa",
        description="beethoven-qa: the verification gate and the pinned copy of this package.",
    )
    parser.add_argument("--version", action="version", version=f"beethoven-qa {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text, configure in _COMMANDS:
        configure(sub.add_parser(name, help=text))
    return parser


def _gate_parser(g: argparse.ArgumentParser) -> None:
    g.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    g.add_argument(
        "--module",
        metavar="DOTTED",
        help="only this module of the package (for example beethoven_qa.vendor)",
    )
    g.add_argument(
        "--report-all",
        action="store_true",
        help="accepted and without effect: every run prints every condition's verdict and fails "
        "on a precondition no input can meet",
    )
    g.add_argument(
        "--base",
        metavar="REF",
        default=os.environ.get("BEETHOVEN_QA_BASE") or "none",
        help="prove only the modules the change since REF reaches (pyproject.toml read by its "
        "keys); REF is a ref, 'auto' (where HEAD left the remote's default branch) or 'none' "
        "(every module; the default, unless BEETHOVEN_QA_BASE names another); never under "
        "--module, which proves its module whole",
    )


def _standards_parser(s: argparse.ArgumentParser) -> None:
    s.add_argument("group", choices=[*standards.GROUPS, "all"], help="one group, or all of them")
    s.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    s.add_argument(
        "--base",
        default="auto",
        help="the commit the new code starts from: a ref, 'auto' (where HEAD left the remote's "
        "default branch; the default) or 'none' (the whole tree)",
    )
    s.add_argument("--sarif", type=Path, help="write each group's SARIF 2.1.0 log into DIR")


def _coach_parser(c: argparse.ArgumentParser) -> None:
    c.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")


def _vendor_parser(v: argparse.ArgumentParser) -> None:
    v.add_argument("--into", type=Path, required=True, help="root of the consuming repository")
    v.add_argument(
        "--ref",
        required=True,
        help="the release tag to take from GitHub (a commit on main is recorded as untagged)",
    )
    v.add_argument("--kit", action="store_true", help="also put the repository kit files in place")


def _vendor_check_parser(c: argparse.ArgumentParser) -> None:
    c.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    c.add_argument(
        "--source",
        type=Path,
        help=(
            "the beethoven-qa checkout this command runs from (its own environment): also rebuild "
            "the copy from the commit its ORIGIN names; refused from anywhere else"
        ),
    )


def _boundary_parser(b: argparse.ArgumentParser) -> None:
    b.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")


def _report_parser(r: argparse.ArgumentParser) -> None:
    r.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    r.add_argument(
        "--out",
        type=Path,
        default=Path("report"),
        help="where report.md, report.json, every lane's output and the SARIF logs are written "
        "(default: ./report); refused unless empty or not there yet",
    )
    r.add_argument(
        "--base",
        metavar="REF",
        default=None,
        help="the commit the new code starts from, handed to the standards lane as its --base "
        "(default: the lane's own, auto: the merge base with origin/main)",
    )
    r.add_argument(
        "--timeout",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="how long one lane may run before it is stopped and recorded as exit 124 "
        "(default: 3600)",
    )


# Each command, its help line and what adds its arguments, in the order --help lists them.
_COMMANDS: tuple[tuple[str, str, Callable[[argparse.ArgumentParser], None]], ...] = (
    (
        "gate",
        "every public function has contracts, and CrossHair + Z3 find no counterexample",
        _gate_parser,
    ),
    (
        "standards",
        "the code standards, each group on its own, on the code new since a base commit",
        _standards_parser,
    ),
    (
        "coach",
        "for Claude Code's PostToolUse hook: what the cheap groups would block in the file an edit "
        "touched, as the hook's JSON on stdout, or nothing",
        _coach_parser,
    ),
    (
        "vendor",
        "write the pinned copy of a release, read from GitHub, into a repository",
        _vendor_parser,
    ),
    ("vendor-check", "the copy matches its ORIGIN and the kit is in place", _vendor_check_parser),
    (
        "boundary",
        "the interface standard's rules: a sealed tree, the manifest, pinned imports, the "
        "published surface, the direction",
        _boundary_parser,
    ),
    (
        "report",
        "the evidence report: every lane of the gate run as its own process and written up, with "
        "who decided each verdict, for anyone who doubts code written by an AI",
        _report_parser,
    ),
)


def _gate(args: argparse.Namespace) -> int:
    try:
        config = gate.GateConfig.from_pyproject(args.root.resolve())
    except (OSError, ValueError) as exc:  # a missing file, bad TOML, or pydantic's refusal
        print(f"gate: the [tool.beethoven-qa] table cannot be used: {exc}")
        return 2
    if args.module is not None and not args.module.startswith(config.package):
        print(f"gate: {args.module} is not part of {config.package}")
        return 2
    # --report-all is accepted for the workflow scripts that pass it; it is the default. --module
    # proves its module whole, whatever the base, since a person asked for that module.
    base = "none" if args.module is not None else args.base
    return gate._run(args.root.resolve(), args.module, base)


def _standards(args: argparse.Namespace) -> int:
    names = list(standards.GROUPS) if args.group == "all" else [args.group]
    return standards._run(args.root, names, args.base, args.sarif)


# The commands that ask git: a git that fails or is missing is exit 2, never a traceback.
_GIT_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "vendor": lambda args: vendor._sync(args.into, args.kit, args.ref),
    "vendor-check": lambda args: vendor._check(args.root, args.source),
    "boundary": lambda args: boundary._run(args.root),
}


def _coach(args: argparse.Namespace) -> int:
    # Always 0: an answer is advice beside an edit that has already happened, and an exit code
    # Claude Code reads as a failure would turn a missing tool into noise in the session.
    answer = coach._run(args.root, sys.stdin.read())
    if answer:
        print(answer)
    return 0


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.command == "gate":
        return _gate(args)
    if args.command == "standards":
        return _standards(args)
    if args.command == "coach":
        return _coach(args)
    if args.command == "report":
        return report._run(args.root, args.out, report._Options(args.base, args.timeout))
    try:
        return _GIT_COMMANDS[args.command](args)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"{args.command}: git failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
