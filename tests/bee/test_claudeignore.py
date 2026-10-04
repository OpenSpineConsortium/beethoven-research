"""``.claudeignore``: gitignore semantics, the tool payloads, the native deny rules."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from beethoven_research.bee import claudeignore as ci

TEMPLATE = """# comment
node_modules/
/build
*.log
!keep.log
docs/**/draft-*.md
secret?.txt
[Tt]emp/
"""


@pytest.fixture
def rules() -> tuple[ci.Rule, ...]:
    return ci.compile_rules(TEMPLATE)


@pytest.mark.parametrize(
    ("rel", "is_dir", "pattern"),
    [
        ("node_modules", True, "node_modules/"),
        ("node_modules/x/y.js", False, "node_modules/"),
        ("pkg/node_modules/z.js", False, "node_modules/"),
        ("node_modules", False, ""),  # a file of that name is not the folder
        ("build", True, "/build"),
        ("build/out.o", False, "/build"),
        ("src/build/x", False, ""),  # anchored to the root
        ("a/b/run.log", False, "*.log"),
        ("keep.log", False, ""),  # re-included
        ("docs/x/y/draft-1.md", False, "docs/**/draft-*.md"),
        ("docs/draft-1.md", False, "docs/**/draft-*.md"),
        ("secret1.txt", False, "secret?.txt"),
        ("secret10.txt", False, ""),
        ("Temp/a", False, "[Tt]emp/"),
        ("src/main.py", False, ""),
    ],
)
def test_gitignore_semantics(
    rules: tuple[ci.Rule, ...], rel: str, is_dir: bool, pattern: str
) -> None:
    assert ci.ignored_by(rules, rel, is_dir) == pattern


def test_an_ignored_folder_cannot_be_reopened_inside() -> None:
    rules = ci.compile_rules("data/\n!data/keep.csv\n")
    assert ci.ignored_by(rules, "data/keep.csv", False) == "data/"


def test_comments_blanks_and_escapes() -> None:
    rules = ci.compile_rules("\n# x\n\\#literal\n   \n")
    assert [r.source for r in rules] == ["\\#literal"]
    assert ci.ignored_by(rules, "#literal", False) == "\\#literal"


def test_deny_rules(rules: tuple[ci.Rule, ...]) -> None:
    assert ci.deny_rules(rules) == ()  # a ! line: the hook alone
    plain = ci.compile_rules("node_modules/\n/build\n*.log\n")
    assert ci.deny_rules(plain) == (
        "Read(./**/node_modules/**)",
        "Read(./build)",
        "Read(./build/**)",
        "Read(./**/*.log)",
        "Read(./**/*.log/**)",
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".claudeignore").write_text(TEMPLATE)
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def ask(tool: str, cwd: Path, **tool_input: object) -> str | None:
    return ci.decide({"tool_name": tool, "cwd": str(cwd), "tool_input": tool_input})


def test_the_file_tools(repo: Path) -> None:
    assert "node_modules/" in (ask("Read", repo, file_path=str(repo / "node_modules/x/a.js")) or "")
    assert ask("Read", repo, file_path=str(repo / "src/a.py")) is None
    assert ask("Edit", repo, file_path="build/x.c") is not None  # relative to cwd
    assert ask("NotebookEdit", repo, notebook_path=str(repo / "a.log")) is not None
    assert ask("Grep", repo, pattern="x", path=str(repo / "node_modules")) is not None
    assert ask("Grep", repo, pattern="x", path=str(repo / "src")) is None
    assert ask("Glob", repo, pattern="node_modules/**/*.js") is not None
    assert ask("Glob", repo, pattern="**/*.py") is None
    assert ask("Bash", repo, command="cat node_modules/x") is None  # not a file tool
    assert ask("Read", repo, file_path="/etc/hostname") is None  # outside the project


def test_sign_in_file_is_refused_everywhere(repo: Path) -> None:
    assert "sign-in" in (ask("Read", repo, file_path="/any/where/.credentials.json") or "")


def test_links_are_judged_where_they_lead(repo: Path) -> None:
    os.symlink(repo / "node_modules", repo / "src" / "nm")
    assert ask("Read", repo, file_path=str(repo / "src/nm/x/a.js")) is not None


def test_no_file_means_no_opinion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert ask("Read", tmp_path, file_path=str(tmp_path / "node_modules/a")) is None


def test_main_denies_with_json_and_fails_open(repo: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    payload = {"tool_name": "Read", "cwd": str(repo),
               "tool_input": {"file_path": str(repo / "x.log")}}  # fmt: skip
    assert ci.main(io.StringIO(json.dumps(payload)), out, err) == 0
    decision = json.loads(out.getvalue())["hookSpecificOutput"]
    assert (
        decision["permissionDecision"] == "deny" and "*.log" in decision["permissionDecisionReason"]
    )
    out = io.StringIO()
    assert ci.main(io.StringIO("[]"), out, err) == 1
    assert out.getvalue() == "" and "allowed" in err.getvalue()
