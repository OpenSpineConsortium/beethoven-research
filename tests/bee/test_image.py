"""What the image carries for bee: the managed settings name its hooks, and the recipe bakes them.

The CI image lane runs them inside a built image; these checks keep the three files in step, so a
renamed hook or a forgotten ``%files`` line fails here first rather than as a silent no-op in a job.
"""

from __future__ import annotations

import json
import re

from .conftest import REPO

MANAGED = REPO / "containers" / "managed-settings.json"
RECIPE = REPO / "containers" / "claude-verify.def"
HOOKS = "/opt/beethoven-research/hooks/"
PYTHON = "/opt/beethoven-research/.pixi/envs/default/bin/python "


def _commands(settings: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    return {
        event: [h for group in groups for h in group["hooks"]] for event, groups in hooks.items()
    }


def test_managed_settings_name_every_bee_hook() -> None:
    settings = json.loads(MANAGED.read_text())
    by_event = _commands(settings)
    expected = {
        "SessionStart": "handoff.py session-start",
        "PostToolUse": "handoff.py post-tool-use",
        "PreCompact": "handoff.py pre-compact",
        "Stop": "handoff.py stop",
        "StopFailure": "handoff.py stop-failure",
        "PreToolUse": "claudeignore.py",
    }
    for event, tail in expected.items():
        assert any(h["command"] == PYTHON + HOOKS + tail for h in by_event[event]), event
    post = [h for h in by_event["PostToolUse"] if "handoff.py" in str(h["command"])]
    assert post[0]["async"] is True  # git status after every edit never holds the session up
    assert settings["statusLine"]["command"] == PYTHON + HOOKS + "handoff.py statusline"
    # The ignore hook judges the same tools bee init wires on a laptop.
    from beethoven_research.bee import cli

    groups = settings["hooks"]["PreToolUse"]
    ignore = [g for g in groups if g["hooks"][0]["command"].endswith("claudeignore.py")]
    assert ignore[0]["matcher"] == cli.IGNORE_MATCHER
    wired = {event: matcher for event, matcher, _, _ in cli.HOOK_EVENTS}
    for event, groups in settings["hooks"].items():
        if event in wired:
            assert groups[0].get("matcher", "") == wired[event], event


def test_recipe_bakes_the_files_the_settings_name() -> None:
    recipe = RECIPE.read_text()
    named = set(re.findall(re.escape(HOOKS) + r"([a-z]+\.py)", MANAGED.read_text()))
    assert named == {"guard.py", "handoff.py", "claudeignore.py"}
    for script in named:
        source = "src/beethoven_research/" + ("" if script == "guard.py" else "bee/") + script
        assert f"{source} {HOOKS}{script}" in recipe, script
        assert (REPO / source).exists()


def test_no_bee_hook_opens_the_transcript() -> None:
    # The session hook records the transcript's path for the next account; nothing of bee opens
    # it (a fork hands the path to Claude Code itself, docs/ACCOUNTS.md section 5).
    for name in ("handoff.py", "claudeignore.py", "accounts.py", "cli.py"):
        text = (REPO / "src" / "beethoven_research" / "bee" / name).read_text()
        assert not re.search(r"open\([^)]*transcript", text), name
