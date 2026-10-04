"""The account list and the switch: pure logic sampled here, proved by the gate; the edges run."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from icontract import ViolationError
from pydantic import ValidationError

from beethoven_research.bee import accounts as acc

A = acc.Account("a", "/h/a", True, 0)
B = acc.Account("b", "/h/b", True, 1)
C = acc.Account("c", None, True, 2)
ABC = (A, B, C)
NOW = 1_800_000_000


def test_labels() -> None:
    for good in ("main", "second", "lab-2", "a" * 32, "0"):
        assert acc.valid_label(good)
    for bad in ("", "Main", "-x", "a b", "a_b", "a" * 33, "é", "../x"):
        assert not acc.valid_label(bad)


def test_absolute_on_both_families() -> None:
    for p in ("/x", "\\\\server\\share", "C:\\Users\\g", "c:/Users/g"):
        assert acc.is_absolute(p)
    for p in ("x", "./x", "~/x", "C:x", ""):
        assert not acc.is_absolute(p)


def test_add_appends_last_and_enabled() -> None:
    out = acc.add_account(ABC, "d", "/h/d")
    assert out[:3] == ABC and out[3] == acc.Account("d", "/h/d", True, 3)
    with pytest.raises(ViolationError):  # a label names one account
        acc.add_account(ABC, "a", "/h/x")
    with pytest.raises(ViolationError):  # one directory is one account
        acc.add_account(ABC, "d", "/h/a")
    with pytest.raises(ViolationError):  # only one account may be the default place
        acc.add_account(ABC, "d", None)


def test_edits_touch_only_the_named_account() -> None:
    assert acc.set_enabled(ABC, "b", False) == (A, acc.Account("b", "/h/b", False, 1), C)
    assert acc.remove_account(ABC, "b") == (A, C)
    rested = acc.set_hibernation(ABC, "a", NOW + 10)
    assert rested[0].hibernate_until == NOW + 10 and rested[1:] == (B, C)
    assert acc.set_hibernation(rested, "a", 0)[0] == A


def test_rotation_order_skips_current_off_and_resting() -> None:
    assert acc.choose_next(ABC, "a", NOW, "order") == "b"
    assert acc.choose_next(ABC, "c", NOW, "order") == "a"  # wraps
    assert acc.choose_next(ABC, "", NOW, "order") == "a"
    off = acc.set_enabled(ABC, "b", False)
    assert acc.choose_next(off, "a", NOW, "order") == "c"
    resting = acc.set_hibernation(ABC, "b", NOW + 60)
    assert acc.choose_next(resting, "a", NOW, "order") == "c"
    awake = acc.set_hibernation(ABC, "b", NOW - 1)
    assert acc.choose_next(awake, "a", NOW, "order") == "b"
    assert acc.choose_next((A,), "a", NOW, "order") == ""


def test_strategies_use_the_status_line_readings() -> None:
    usage = (
        acc.Usage("a", 95, NOW + 600),
        acc.Usage("b", 60, NOW + 3000),
        acc.Usage("c", 20, NOW + 9000),
    )
    # a is current; b and c may take over.
    assert acc.choose_next(ABC, "a", NOW, "order", usage) == "b"
    assert acc.choose_next(ABC, "a", NOW, "best", usage) == "c"
    assert acc.choose_next(ABC, "a", NOW, "consume-first", usage) == "b"
    # An account at the threshold less the margin is not a candidate; a past window is fresh.
    near = (acc.Usage("b", 86, NOW + 3000), acc.Usage("c", 99, NOW - 1))
    assert acc.choose_next(ABC, "a", NOW, "order", near) == "c"
    assert acc.choose_next(ABC, "a", NOW, "order", near, threshold=90, hysteresis=0) == "b"
    # A spent week keeps an account out until the week resets.
    weekly = (acc.Usage("b", 0, 0, 100, NOW + 86400),)
    assert acc.choose_next(ABC, "a", NOW, "order", weekly) == "c"
    # consume-first prefers a running window to none at all.
    running = (acc.Usage("c", 30, NOW + 100),)
    assert acc.choose_next(ABC, "a", NOW, "consume-first", running) == "c"


def test_hand_off_rule() -> None:
    assert acc.should_hand_off(10, 90, 0, 300, True)  # a hit limit always hands off
    assert acc.should_hand_off(91, 90, 301, 300, False)
    assert not acc.should_hand_off(91, 90, 100, 300, False)  # cooldown
    assert not acc.should_hand_off(80, 90, 1000, 300, False)


def test_rest_ends_at_the_known_reset() -> None:
    assert acc.hibernate_until(NOW, NOW + 100) == NOW + 100
    assert acc.hibernate_until(NOW, 0) == NOW + acc.FALLBACK_REST_S
    assert acc.hibernate_until(NOW, NOW - 5) == NOW + acc.FALLBACK_REST_S
    assert acc.hibernate_until(NOW, NOW + 100, 100, NOW + 86400) == NOW + 86400


def test_env_lines_round_trip_through_a_shell(tmp_path: Path) -> None:
    odd = acc.Account("x", "/tmp/it's a $dir", True, 0)
    lines = acc.render_env(acc.env_pairs(odd), "posix")
    out = subprocess.run(
        ["bash", "-c", lines + 'printf "%s|%s" "$CLAUDE_CONFIG_DIR" "$BEE_ACCOUNT"'],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": "/old"},
    )
    assert out.stdout == "/tmp/it's a $dir|x"
    unset = acc.render_env(acc.env_pairs(C), "posix")
    out = subprocess.run(
        ["bash", "-c", unset + 'printf "%s" "${CLAUDE_CONFIG_DIR-unset}"'],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": "/old"},
    )
    assert out.stdout == "unset"
    ps = acc.render_env(acc.env_pairs(odd), "powershell")
    assert ps == "$env:CLAUDE_CONFIG_DIR = '/tmp/it''s a $dir'\n$env:BEE_ACCOUNT = 'x'\n"
    assert acc.render_env(acc.env_pairs(C), "powershell").startswith(
        "Remove-Item Env:CLAUDE_CONFIG_DIR"
    )


def test_child_environment_loses_every_credential() -> None:
    parent = {name: "secret" for name in acc.CREDENTIAL_VARS}
    parent.update(PATH="/bin", HOME="/h", CLAUDE_CONFIG_DIR="/old", BEE_ACCOUNT="old")
    assert acc.clean_child_env(parent) == {"PATH": "/bin", "HOME": "/h"}


# What `claude auth status --json` answered in a cloud session for a directory nobody signed in to
# (2026-10-04, Claude Code 2.1.289): a token from the host outranked the directory.
MEASURED_HOST_TOKEN = {
    "loggedIn": True,
    "authMethod": "oauth_token",
    "apiProvider": "firstParty",
    "analyticsDisabled": False,
    "projectsDirectory": "/x/emptycfg/a/projects",
    "configDirectory": "/x/emptycfg/a",
}


def test_status_counts_only_the_directorys_own_subscription() -> None:
    ok = {"loggedIn": True, "authMethod": "claude.ai", "configDirectory": "/d"}
    assert acc.interpret_status(ok, "/d").ok
    assert not acc.interpret_status(MEASURED_HOST_TOKEN, "/x/emptycfg/a").ok
    assert "environment" in acc.interpret_status(MEASURED_HOST_TOKEN, None).reason
    assert "bills" in acc.interpret_status({**ok, "authMethod": "api_key"}, "/d").reason
    assert not acc.interpret_status({**ok, "apiProvider": "bedrock"}, "/d").ok
    assert not acc.interpret_status({**ok, "configDirectory": "/other"}, "/d").ok
    assert not acc.interpret_status({"loggedIn": False}, "/d").ok
    old = {"loggedIn": True, "authMethod": "claude.ai"}
    assert acc.interpret_status(old, "/d").ok  # before configDirectory existed


def test_transcript_seen_from_the_host() -> None:
    got = acc.host_transcript(
        "/claude/projects/-work/abc.jsonl", "/home/g/.beethoven/accounts/b/claude/"
    )
    assert got == "/home/g/.beethoven/accounts/b/claude/projects/-work/abc.jsonl"
    with pytest.raises(ViolationError):
        acc.host_transcript("/claude/../etc/passwd", "/h")
    with pytest.raises(ViolationError):
        acc.host_transcript("/elsewhere/x.jsonl", "/h")


def test_registry_round_trip_and_validation(tmp_path: Path) -> None:
    rested = acc.set_hibernation(ABC, "b", NOW)
    acc.save_accounts(str(tmp_path), rested)
    assert acc.load_accounts(str(tmp_path)) == rested
    path = tmp_path / "accounts.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    raw = json.loads(path.read_text())
    assert raw["version"] == 1 and raw["accounts"][1]["hibernate_until"].endswith("Z")
    assert acc.load_accounts(str(tmp_path / "absent")) == ()
    raw["accounts"].append(dict(raw["accounts"][0]))
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        acc.load_accounts(str(tmp_path))
    path.write_text(
        json.dumps({"version": 1, "accounts": [{"label": "x", "path": "rel", "order": 0}]})
    )
    with pytest.raises(ValidationError):
        acc.load_accounts(str(tmp_path))


def test_usage_readings(tmp_path: Path) -> None:
    (tmp_path / "usage").mkdir()
    (tmp_path / "usage" / "a.json").write_text(
        json.dumps({"five_hour_pct": 42, "five_hour_resets_at": NOW, "seven_day_pct": None})
    )
    (tmp_path / "usage" / "b.json").write_text("not json")
    assert acc.load_usage(str(tmp_path), ABC) == (acc.Usage("a", 42, NOW, 0, 0),)


def test_main_links_the_launchers_sign_in(home: Path) -> None:
    launcher = home / ".beethoven" / "claude"
    launcher.mkdir(parents=True)
    path = acc.prepare_account_dir("main")
    assert path == str(home / ".beethoven" / "accounts" / "main" / "claude")
    assert os.path.islink(path) and os.path.realpath(path) == str(launcher)
    assert oct(launcher.stat().st_mode & 0o777) != "0o000"
    other = acc.prepare_account_dir("second")
    assert os.path.isdir(other) and not os.path.islink(other)
    assert oct(Path(other).stat().st_mode & 0o777) == "0o700"


def test_login_node_is_refused(tmp_path: Path) -> None:
    sbatch = tmp_path / "sbatch"
    sbatch.write_text("#!/bin/sh\n")
    sbatch.chmod(0o755)
    env = {"PATH": str(tmp_path)}
    assert "login node" in acc.login_node_refusal(env)
    assert "login node" in acc.login_node_refusal({**env, "SLURM_JOB_ID": "1"})  # an salloc shell
    assert acc.login_node_refusal({**env, "SLURM_JOB_ID": "1", "SLURMD_NODENAME": "n1"}) == ""
    assert acc.login_node_refusal({"PATH": "/nonexistent"}) == ""
