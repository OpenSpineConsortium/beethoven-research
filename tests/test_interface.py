"""The interface standard's rules for this repository (interface/README.md).

It offers nothing; it takes beethoven-qa's four exports into one pinned copy; its code imports only
what beethoven-qa's `python` export lists and nothing of beethoven-agents until that is declared;
and no tracked line reaches another repository's checkout. beethoven-qa's own `boundary` command
checks the same since the re-pin to 2.0.0 (the gate's `boundary` lane); this file keeps its own
check beside it.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO / "interface" / "INTERFACE.json").read_text(encoding="utf-8"))
QA = "OpenSpineConsortium/beethoven-qa"
COPY = "vendor/beethoven-qa"

# beethoven-qa's `python` export, version 1 (its interface/python.json): what may be imported.
# Held here as well as in the pinned copy; the last test fails when the two part.
PYTHON_EXPORT = {
    "beethoven_qa": ["__version__"],
    "beethoven_qa.contracts": ["DBC", "ViolationError", "ensure", "invariant", "require"],
}

OTHERS = (
    "beethoven", "beethoven-extension", "beethoven-desktop", "beethoven-qa", "beethoven-agents",
    "beethoven-sites", "beethoven-base", "beethoven-vscode", "hpcworker", "ashley",
    "ashley-bridge", "openspineconsortium.github.io", "spinesurg-ct-nnunet",
    "u251-xenograft-rat-rnaseq-study",
)  # fmt: skip
_NAMES = "|".join(re.escape(n) for n in sorted(OTHERS, key=len, reverse=True))
# "../<repo>", or a path built from this checkout's parent such as "$(dirname ...)/<repo>"
SIBLING = re.compile(rf"(?:\.\./|\)/|\}}/)({_NAMES})(?![A-Za-z0-9_.-])")
CLONE = re.compile(rf"\b(?:git clone|gh repo clone)\b[^\n]*?OpenSpineConsortium/({_NAMES})\b")


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def python_files() -> list[Path]:
    return [REPO / f for f in tracked() if f.endswith(".py") and not f.startswith("vendor/")]


def imports(path: Path) -> list[tuple[str, str]]:
    """(module, name) for every `from m import n`, and (m, "") for every `import m`."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found += [(node.module, a.name) for a in node.names]
        elif isinstance(node, ast.Import):
            found += [(a.name, "") for a in node.names]
    return found


def test_the_manifest_offers_nothing() -> None:
    assert MANIFEST["format"] == 1
    assert MANIFEST["name"] == "beethoven-research"
    assert MANIFEST["repository"] == "OpenSpineConsortium/beethoven-research"
    assert MANIFEST["exports"] == []


def test_the_imports_are_beethoven_qas_four_exports_into_one_copy() -> None:
    assert sorted(i["export"] for i in MANIFEST["imports"]) == ["cli", "kit", "plugin", "python"]
    # cli is at version 2 from beethoven-qa 3.0.0, for vendor's required --ref
    versions = {"cli": 2, "kit": 1, "plugin": 1, "python": 1}
    for entry in MANIFEST["imports"]:
        assert entry["repository"] == QA
        assert entry["version"] == versions[entry["export"]]
        assert entry["into"] == COPY
        assert entry["origin"] == f"{COPY}/ORIGIN"
    assert (REPO / COPY / "ORIGIN").read_text(encoding="utf-8").startswith(f"repository: {QA}\n")


def test_code_imports_only_what_beethoven_qa_exports() -> None:
    outside = [
        (str(path.relative_to(REPO)), module, name)
        for path in python_files()
        for module, name in imports(path)
        if module.split(".")[0] == "beethoven_qa" and name not in PYTHON_EXPORT.get(module, [])
    ]
    assert outside == []


def test_nothing_imports_beethoven_agents_until_an_import_is_declared() -> None:
    assert not any(i["repository"].endswith("/beethoven-agents") for i in MANIFEST["imports"])
    reached = [
        str(path.relative_to(REPO))
        for path in python_files()
        if any(module.split(".")[0] == "beethoven_agents" for module, _ in imports(path))
    ]
    assert reached == []


def reaches(rel: str) -> list[str]:
    try:
        lines = (REPO / rel).read_text(encoding="utf-8").split("\n")
    except (UnicodeDecodeError, OSError):
        return []
    return [f"{rel}:{n}: {line.strip()[:160]}" for n, line in enumerate(lines, 1)
            if SIBLING.search(line) or CLONE.search(line)]  # fmt: skip


def test_no_tracked_line_reaches_another_checkout() -> None:
    # Prose may name another repository; the copy is beethoven-qa's own tree, held to its record by
    # vendor-check; this file names the others to look for them.
    me = Path(__file__).resolve().relative_to(REPO).as_posix()
    files = [f for f in tracked() if not (f.endswith(".md") or f.startswith("vendor/") or f == me)]
    assert [hit for rel in files for hit in reaches(rel)] == []


def test_the_held_python_export_equals_the_copys() -> None:
    published = REPO / COPY / "interface" / "python.json"
    assert published.exists(), "the pinned copy is older than beethoven-qa 2.0.0; take 2.0.0"
    assert json.loads(published.read_text(encoding="utf-8")) == PYTHON_EXPORT
