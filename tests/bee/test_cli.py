"""The ``bee`` command against a fake Claude Code: the list, the switch, the hand-off, init."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from beethoven_research.bee import accounts as acc
from beethoven_research.bee import cli, handoff

from .conftest import FAKES, REPO, signed_in

NOW = acc.now()


def bee(*args: str) -> int:
    return cli.main(list(args))


def log(tmp_path: Path) -> str:
    p = tmp_path / "claude.log"
    return p.read_text() if p.exists() else ""


def three(home: Path) -> dict[str, str]:
    """Accounts a and b signed in, c listed but never signed in."""
    paths = {}
    for label in ("a", "b", "c"):
        assert bee("account", "add", label, "--no-login") == 0
        paths[label] = str(home / ".beethoven" / "accounts" / label / "claude")
    signed_in(paths["a"], "a")
    signed_in(paths["b"], "b")
    return paths


def test_add_signs_in_through_claude_codes_own_login(home: Path, tmp_path: Path) -> None:
    assert bee("account", "add", "main") == 0
    path = home / ".beethoven" / "accounts" / "main" / "claude"
    assert (path / ".fake-signed-in").exists()  # the fake's own marker, made by "auth login"
    assert f"login {path}" in log(tmp_path)
    registry = json.loads((home / ".beethoven" / "engine" / "accounts.json").read_text())
    assert registry["accounts"] == [
        {"label": "main", "path": str(path), "enabled": True, "order": 0, "hibernate_until": None}
    ]
    assert bee("account", "add", "main") == 1  # once
    assert bee("account", "add", "Bad Label") == 1


def test_default_location_is_the_variable_unset(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bee("account", "add", "personal", "--default-location", "--no-login") == 0
    capsys.readouterr()
    assert bee("account", "env", "personal") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["unset CLAUDE_CONFIG_DIR", "export BEE_ACCOUNT='personal'"]
    # and every credential variable, so a plain claude after the switch uses only that sign-in
    assert lines[2:] == [f"unset {name}" for name in acc.CREDENTIAL_VARS]


def test_list_status_enable_remove(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = three(home)
    capsys.readouterr()
    assert bee("account", "status") == 1  # c is not signed in
    out = capsys.readouterr().out
    assert (
        "a            signed in (a@example.org, max)" in out and "c            not signed in" in out
    )
    assert bee("account", "disable", "b") == 0
    assert bee("account", "list", "--json") == 0
    capsys.readouterr()
    bee("account", "list", "--json")
    rows = json.loads(capsys.readouterr().out)
    assert [r["enabled"] for r in rows] == [True, False, True]
    assert bee("account", "remove", "c") == 0
    assert os.path.isdir(paths["c"])  # the directory is never touched
    assert bee("account", "path", "c") == 1


def test_next_and_rest(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    three(home)
    capsys.readouterr()
    assert bee("account", "next", "--from", "a") == 0
    assert capsys.readouterr().out.strip() == "b"
    assert bee("account", "spent", "b", "--hours", "1") == 0
    capsys.readouterr()
    bee("account", "next", "--from", "a")
    assert capsys.readouterr().out.strip() == "c"
    assert bee("account", "rested", "b") == 0
    capsys.readouterr()
    bee("account", "next", "--from", "a")
    assert capsys.readouterr().out.strip() == "b"


def test_continue_hands_over_with_the_hydration_prompt(
    home: Path, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = three(home)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-pass")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "must-not-pass")
    sdir = handoff.state_dir(str(project))
    resets = NOW + 3600
    handoff.write_record(sdir, handoff.SESSION_FILE, {"session_id": "s1", "account": "a"})
    handoff.write_record(sdir, handoff.LIMIT_FILE, {"account": "a", "five_hour_resets_at": resets})
    assert bee("continue", "--project", str(project), "--once") == 0
    seen = log(tmp_path)
    assert "argv [Read .claude/state/HYDRATE.md and follow it.]" in seen
    assert f"env CLAUDE_CONFIG_DIR={paths['b']}" in seen and "env BEE_ACCOUNT=b" in seen
    assert "env BEETHOVEN_HANDOFF=1" in seen and "env BEETHOVEN_HANDOFF_GATE=1" in seen
    assert "env ANTHROPIC_API_KEY=<unset>" in seen and "env CLAUDE_CODE_OAUTH_TOKEN=<unset>" in seen
    resting = acc.load_accounts(acc.registry_dir())[0]
    assert resting.label == "a" and resting.hibernate_until == resets
    assert handoff.read_record(sdir, handoff.LIMIT_FILE)["handled"]
    assert (Path(sdir) / "HYDRATE.md").exists() and (Path(sdir) / "PROJECT_STATE.md").exists()
    switches = (Path(acc.registry_dir()) / "switches.log").read_text()
    assert "limit account=a" in switches and "file from=a to=b" in switches


def test_an_account_that_is_not_signed_in_leaves_the_rotation(
    home: Path, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = three(home)
    os.unlink(Path(paths["b"]) / ".fake-signed-in")
    sdir = handoff.state_dir(str(project))
    handoff.write_record(sdir, handoff.SESSION_FILE, {"session_id": "s1", "account": "a"})
    assert bee("continue", "--project", str(project), "--once") == 1
    err = capsys.readouterr()
    assert "no other account can take over" in err.err
    assert [a.enabled for a in acc.load_accounts(acc.registry_dir())] == [True, False, False]
    assert "argv" not in log(tmp_path)


def test_run_and_a_named_account(home: Path, project: Path, tmp_path: Path) -> None:
    paths = three(home)
    assert bee("run", "b", "--project", str(project), "--once", "--", "--model", "x") == 0
    seen = log(tmp_path)
    assert "argv [--model] [x]" in seen and f"env CLAUDE_CONFIG_DIR={paths['b']}" in seen
    assert "env BEETHOVEN_HANDOFF=<unset>" in seen
    assert bee("run", "c", "--project", str(project), "--once") == 1  # not signed in


def test_fork_resumes_the_recorded_transcript(home: Path, project: Path, tmp_path: Path) -> None:
    paths = three(home)
    transcript = Path(paths["a"]) / "projects" / "-proj" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    sdir = handoff.state_dir(str(project))
    handoff.write_record(
        sdir,
        handoff.SESSION_FILE,
        {"session_id": "s1", "account": "a", "transcript_path": str(transcript)},
    )
    assert bee("continue", "--fork", "--project", str(project), "--once") == 0
    seen = log(tmp_path)
    assert f"argv [--resume] [{transcript}] [--fork-session]" in seen
    assert "env BEETHOVEN_HANDOFF=<unset>" in seen


@pytest.mark.parametrize(
    ("record", "why"),
    [
        ({"session_id": "s1", "account": "a", "transcript_path": "/claude/.credentials.json"},
         "not a session file"),
        ({"session_id": "s1", "account": "a",
          "transcript_path": "/claude/projects/-work/../../.credentials.json"},
         "not a session file"),
        ({"session_id": "s2", "account": "a", "transcript_path": "/claude/projects/-work/s1.jsonl"},
         "not a session file"),
        ({"session_id": "s1", "account": "zz", "transcript_path": "/claude/projects/w/s1.jsonl"},
         "none of your accounts"),
        ({"session_id": "../s1", "account": "a", "transcript_path": "/claude/projects/x/s1.jsonl"},
         "none of your accounts"),
    ],
)  # fmt: skip
def test_fork_believes_only_the_writing_accounts_session_file(
    home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    record: dict[str, str],
    why: str,
) -> None:
    """The session record sits in the project, which a session can write: a path to a sign-in file
    or into another folder must never reach a bind or --resume."""
    three(home)
    handoff.write_record(handoff.state_dir(str(project)), handoff.SESSION_FILE, record)
    capsys.readouterr()
    assert bee("plan", "continue", "--fork", "--project", str(project)) == 1
    out = capsys.readouterr()
    assert out.out == "" and why in out.err


def test_fork_refuses_a_file_outside_the_accounts_projects_on_a_laptop(
    home: Path, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = three(home)
    key = Path(paths["a"]) / ".credentials.json"
    elsewhere = tmp_path / "s1.jsonl"
    elsewhere.write_text("{}\n")
    link = Path(paths["a"]) / "projects" / "-proj" / "s1.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(key)  # a session file name that points at the sign-in file
    for path in (key, elsewhere, link):
        handoff.write_record(
            handoff.state_dir(str(project)),
            handoff.SESSION_FILE,
            {"session_id": "s1", "account": "a", "transcript_path": str(path)},
        )
        assert bee("continue", "--fork", "--project", str(project), "--once") == 1
        assert "not a session file" in capsys.readouterr().err
    assert "argv" not in log(tmp_path)


def test_the_grid_plan_is_one_line_with_its_notes_on_stderr(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    three(home)
    sdir = handoff.state_dir(str(project))
    handoff.write_record(sdir, handoff.LIMIT_FILE, {"account": "a", "five_hour_resets_at": 0})
    capsys.readouterr()
    assert bee("plan", "continue", "--project", str(project)) == 0
    out = capsys.readouterr()
    assert out.out.count("\n") == 1 and out.out.split("\t")[0] == "b"
    assert "reached its usage limit" in out.err and "No hand-off file" in out.err


def test_project_settings_that_bill_another_way_stop_the_session(
    home: Path, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    three(home)
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    for body in ({"apiKeyHelper": "echo x"}, {"env": {"ANTHROPIC_API_KEY": "x"}}):
        settings.write_text(json.dumps(body))
        assert bee("run", "a", "--project", str(project), "--once") == 1
        err = capsys.readouterr().err
        assert "own subscription" in err and "x" not in err.split("(")[1].split(":")[0]
    assert "argv" not in log(tmp_path)
    settings.write_text(json.dumps({"env": {"OTHER": "1"}}))
    assert bee("run", "a", "--project", str(project), "--once") == 0


def test_the_grid_plan_maps_a_container_transcript_to_the_host(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = three(home)
    sdir = handoff.state_dir(str(project))
    handoff.write_record(
        sdir,
        handoff.SESSION_FILE,
        {"session_id": "s1", "account": "a", "transcript_path": "/claude/projects/-work/s1.jsonl"},
    )
    capsys.readouterr()
    assert bee("plan", "continue", "--fork", "--project", str(project)) == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert fields[:4] == ["b", paths["b"], "fork", paths["a"] + "/projects/-work/s1.jsonl"]
    assert fields[5] == paths["a"]  # the writing account's directory, for the host's own check
    assert bee("plan", "continue", "--project", str(project)) == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert fields[2] == "file" and fields[4].startswith("Read .claude/state/HYDRATE.md")


def test_nothing_starts_inside_a_claude_code_session(
    home: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    three(home)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert bee("run", "a", "--project", str(project), "--once") == 1
    assert "Claude Code session" in capsys.readouterr().err
    assert bee("account", "login", "c") == 1


def test_pin_and_unpin(home: Path, project: Path) -> None:
    three(home)
    (project / ".envrc").write_text("export OTHER=1\n")
    assert bee("account", "pin", "b", "--project", str(project)) == 0
    text = (project / ".envrc").read_text()
    assert text.startswith("export OTHER=1\n") and "export BEE_ACCOUNT='b'" in text
    assert bee("account", "pin", "a", "--project", str(project)) == 0
    assert (project / ".envrc").read_text().count(cli.ENVRC_BEGIN) == 1
    assert "unset ANTHROPIC_API_KEY" in (project / ".envrc").read_text()
    assert bee("account", "unpin", "--project", str(project)) == 0
    assert (project / ".envrc").read_text() == "export OTHER=1\n"


def test_pin_leaves_an_envrc_with_a_broken_block_alone(home: Path, project: Path) -> None:
    three(home)
    text = f"{cli.ENVRC_BEGIN}\nexport BEE_ACCOUNT='a'\nlayout python3\ndotenv .env.local\n"
    (project / ".envrc").write_text(text)
    assert bee("account", "unpin", "--project", str(project)) == 1
    assert bee("account", "pin", "b", "--project", str(project)) == 1
    assert (project / ".envrc").read_text() == text


def test_handoff_commands(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from .test_handoff import GUIDE_EXAMPLE

    assert bee("handoff", "check", "--project", str(project)) == 1  # no file yet
    pasted = tmp_path / "paste.md"
    pasted.write_text("```\n" + GUIDE_EXAMPLE + "aws AKIAABCDEFGHIJKLMNOP\n```\n")
    capsys.readouterr()
    assert bee("handoff", "import", str(pasted), "--project", str(project)) == 0
    out = capsys.readouterr().out
    assert "1 secret-shaped strings were replaced" in out and "phase Debugging, complete" in out
    assert bee("handoff", "check", "--project", str(project)) == 0
    capsys.readouterr()
    assert bee("handoff", "prompt", "--web") == 0
    assert "bee handoff import" in capsys.readouterr().out
    assert bee("handoff", "where", "--project", str(project)) == 0
    assert capsys.readouterr().out.strip() == str(project / ".claude" / "state")


def test_init_merges_into_existing_settings(home: Path, project: Path) -> None:
    settings = project / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    mine = {"type": "command", "command": "echo mine"}
    settings.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}, "permissions": {"deny": ["Bash(x)"]}})
    )
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    data = json.loads(settings.read_text())
    assert data["hooks"]["Stop"][0]["hooks"] == [mine]
    commands = [g["hooks"][0]["command"] for e in data["hooks"].values() for g in e]
    hook = f'bash "{REPO}/src/beethoven_research/bee/hook.sh"'
    for name in ("session-start", "post-tool-use", "pre-compact", "stop", "stop-failure",
                 "claudeignore"):  # fmt: skip
        assert f"{hook} {name}" in commands
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["async"] is True
    assert data["statusLine"]["command"] == f"{hook} statusline"
    assert "Bash(x)" in data["permissions"]["deny"]
    assert "Read(./**/node_modules/**)" in data["permissions"]["deny"]
    assert (project / ".claude" / "commands" / "handoff.md").exists()
    assert (project / ".claudeignore").exists()
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert ".claude/settings.local.json" in exclude
    before = settings.read_text()
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    assert settings.read_text() == before  # a second run adds nothing


def test_init_denies_the_sign_in_files_where_they_are(home: Path, project: Path) -> None:
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    deny = json.loads((project / ".claude" / "settings.local.json").read_text())["permissions"]
    assert {"Read(//**/.credentials.json)", "Read(~/.claude/.credentials.json)",
            "Read(~/.beethoven/**/.credentials.json)"} <= set(deny["deny"])  # fmt: skip


def test_init_takes_back_a_rule_gone_from_claudeignore(home: Path, project: Path) -> None:
    (project / ".claudeignore").write_text("*.log\nsecrets/\n")
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    settings = project / ".claude" / "settings.local.json"
    first = json.loads(settings.read_text())["permissions"]["deny"]
    (project / ".claudeignore").write_text("secrets/\n")
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    second = json.loads(settings.read_text())["permissions"]["deny"]
    gone = [r for r in first if r not in second]
    assert gone and all("log" in r for r in gone)
    assert [r for r in first if "secrets" in r] == [r for r in second if "secrets" in r]


def test_init_keeps_a_status_line_set_elsewhere(home: Path, project: Path) -> None:
    claude = project / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"statusLine": {"type": "command"}}))
    assert bee("init", "--project", str(project), "--engine", str(REPO)) == 0
    assert "statusLine" not in json.loads((claude / "settings.local.json").read_text())
    (claude / "settings.json").unlink()
    assert bee("init", "--project", str(project), "--engine", str(REPO), "--no-statusline") == 0
    assert "statusLine" not in json.loads((claude / "settings.local.json").read_text())


def test_init_excludes_from_a_subfolder_and_a_worktree(home: Path, project: Path) -> None:
    sub = project / "sub"
    sub.mkdir()
    assert bee("init", "--project", str(sub), "--engine", str(REPO)) == 0
    exclude = (project / ".git" / "info" / "exclude").read_text().splitlines()
    assert "/sub/.claude/settings.local.json" in exclude
    tree = project.parent / "tree"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", str(tree)], check=True)
    assert bee("init", "--project", str(tree), "--engine", str(REPO)) == 0
    exclude = (project / ".git" / "info" / "exclude").read_text().splitlines()
    assert "/.claude/settings.local.json" in exclude
    status = subprocess.run(
        ["git", "-C", str(tree), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".claude/settings.local.json" not in status and ".claude/state" not in status


def test_init_refuses_a_folder_that_is_not_the_engine(home: Path, project: Path) -> None:
    assert bee("init", "--project", str(project), "--engine", str(FAKES)) == 1


def test_an_old_limit_record_rests_from_the_hit_not_from_now(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    three(home)
    sdir = handoff.state_dir(str(project))
    day_ago = acc.utc_text(NOW - 86400)
    handoff.write_record(sdir, handoff.LIMIT_FILE, {"account": "a", "at": day_ago,
                         "five_hour_resets_at": NOW - 80000})  # fmt: skip
    capsys.readouterr()
    assert bee("plan", "run", "--project", str(project)) == 0
    assert capsys.readouterr().out.split("\t")[0] == "a"  # its window reset long ago
    assert {a.label: a.hibernate_until for a in acc.load_accounts(acc.registry_dir())}["a"] == 0


def test_a_limit_with_the_five_hour_window_far_off_is_the_week(home: Path, project: Path) -> None:
    three(home)
    handoff.write_record(
        handoff.state_dir(str(project)),
        handoff.LIMIT_FILE,
        {
            "account": "a",
            "at": acc.utc_text(NOW),
            "five_hour_pct": 40,
            "five_hour_resets_at": NOW + 7200,
            "seven_day_pct": 99,
            "seven_day_resets_at": NOW + 3 * 86400,
        },  # fmt: skip
    )
    assert bee("plan", "continue", "--project", str(project)) == 0
    rest = {a.label: a.hibernate_until for a in acc.load_accounts(acc.registry_dir())}["a"]
    assert rest == NOW + 3 * 86400


def test_continue_leaves_the_last_sessions_account_not_the_shells(
    home: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    three(home)
    monkeypatch.setenv("BEE_ACCOUNT", "a")  # bee-use a, or a pinned .envrc
    handoff.write_record(
        handoff.state_dir(str(project)), handoff.SESSION_FILE, {"session_id": "s1", "account": "b"}
    )
    capsys.readouterr()
    assert bee("plan", "continue", "--project", str(project)) == 0
    assert capsys.readouterr().out.split("\t")[0] != "b"


def test_the_threshold_is_bounded_and_reaches_the_hooks(
    home: Path, project: Path, tmp_path: Path
) -> None:
    three(home)
    for bad in ("5", "0", "150"):
        with pytest.raises(SystemExit):
            bee("account", "next", "--threshold", bad)
    with pytest.raises(SystemExit):
        bee("account", "spent", "a", "--hours", "1e9")
    assert bee("run", "a", "--project", str(project), "--once", "--threshold", "75") == 0
    assert "env BEE_THRESHOLD=75" in log(tmp_path)


def test_one_sign_in_is_never_listed_twice_through_a_link(home: Path, tmp_path: Path) -> None:
    real = tmp_path / "launcher"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert bee("account", "add", "main", "--path", str(link), "--no-login") == 0
    assert bee("account", "add", "other", "--path", str(real), "--no-login") == 1
