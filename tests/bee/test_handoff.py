"""The hand-off: the state file, the hand-off file's parse, the prompts, the gate, the hooks."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from beethoven_research.bee import handoff as h

from .conftest import commit

GUIDE_EXAMPLE = """# CLAUDE STATE SERIALIZATION

**Timestamp:** 2026-10-04T05:00:00Z **Phase:** Debugging

## 1. Primary Objective

Make the order service return 201 on POST /api/orders.

## 2. Architectural Decisions & Constraints

- Redis for pub/sub, version 7.
- No ORM in the hot path.

## 3. Files Modified & State

| File Path | Status | Key Modifications / Purpose |
| :-: | :-: | :-: |
| `src/orders.py` | Pending | the handler |
| tests/test_orders.py | broken | new case |

## 4. Completed Milestones

1. Schema migrated, verified with the migration test.

## 5. Active Blockers & Errors

- `AssertionError: 500 != 201`
- Hypothesis: the serializer drops the id.

## 6. Immediate Next Actions (Hydration Directives)

1. Run grep to find the serializer.
2. Run the failing test alone.
3. Fix the id field.
"""


def test_truncate_announces_the_cut() -> None:
    assert h.truncate("short", 100) == "short"
    cut = h.truncate("x" * 500, 100)
    assert len(cut) <= 100 and "500 characters" in cut


def test_state_file_is_bounded_and_says_what_it_cut() -> None:
    snap = h.Snapshot(
        repo=True,
        root="/p",
        branch="main",
        head="a" * 40,
        ahead=1,
        behind=0,
        status=tuple(f" M file{i}.py" for i in range(100)),
        diffstat=tuple("y" * 400 for _ in range(50)),
        log=tuple(f"abc{i} 2026-10-04 commit {i}" for i in range(20)),
    )
    text = h.render_state(snap, "2026-10-04T05:00:00Z", "second", "s1", "hand-off file x: absent")
    assert len(text) <= h.INJECT_LIMIT
    assert "Uncommitted changes (100)" in text and "... and 70 more" in text
    assert "1 ahead, 0 behind" in text and "account: second" in text
    bare = h.render_state(h.Snapshot(repo=False), "t", "", "", "line")
    assert "Not a git repository" in bare


def test_fingerprint_follows_the_tree() -> None:
    a = h.Snapshot(repo=True, head="1", status=(" M a",))
    assert h.fingerprint(a) == h.fingerprint(h.Snapshot(repo=True, head="1", status=(" M a",)))
    assert h.fingerprint(a) != h.fingerprint(h.Snapshot(repo=True, head="2", status=(" M a",)))
    assert h.fingerprint(a) != h.fingerprint(h.Snapshot(repo=True, head="1", status=()))


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnop",
        "ghp_" + "A" * 36,
        "github_pat_" + "B" * 30,
        "AKIA" + "C" * 16,
        "hf_" + "d" * 34,
        "xoxb-1234567890-abc",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
        "Bearer " + "e" * 30,
        "sk-proj-" + "f" * 24,
        "AIza" + "G" * 35,
        "ASIA" + "H" * 16,
        "glpat-" + "i" * 20,
        "sk_live_" + "j" * 24,
        "npm_" + "k" * 36,
    ],
)
def test_redaction_catches_published_token_shapes(secret: str) -> None:
    text, n = h.redact(f"before {secret} after")
    assert secret not in text and n >= 1 and "[redacted]" in text


def test_redaction_keeps_ordinary_text_and_key_names() -> None:
    plain = "Run pytest -q; the token budget is 1000; see password policy."
    assert h.redact(plain) == (plain, 0)
    text, n = h.redact("password = hunter22222\napi_key: abcdef123456")
    assert n == 2 and "password = [redacted]" in text and "api_key: [redacted]" in text
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    assert h.redact(pem) == ("[redacted]", 1)


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENG", "AWS_SECRET_ACCESS_KEY=[redacted]"),
        ("DB_PASSWORD=hunter2hunter2", "DB_PASSWORD=[redacted]"),
        ('password = "hunter2hunter2"', 'password = "[redacted]"'),
        ('"api_key": "abcdef123456"', '"api_key": "[redacted]"'),
        ("export SECRET='abcdefghijkl'", "export SECRET='[redacted]'"),
        (
            "postgres://admin:S3cretPassw0rd@db.example.org/app",
            "postgres://admin:[redacted]@db.example.org/app",
        ),
    ],
)
def test_redaction_catches_prefixed_quoted_and_url_secrets(line: str, kept: str) -> None:
    assert h.redact(line) == (kept, 1)
    assert h.redact(kept) == (kept, 0)  # a second pass finds nothing more


def test_redaction_leaves_counts_and_plain_urls() -> None:
    for plain in ("max_tokens: 100000", "context_tokens: 45000", "see https://user@host/x"):
        assert h.redact(plain) == (plain, 0)


def test_parse_the_guides_own_schema() -> None:
    r = h.parse_context(GUIDE_EXAMPLE)
    assert r.ok and r.missing == () and r.present == (1, 2, 3, 4, 5, 6)
    assert r.phase == "Debugging" and r.timestamp == "2026-10-04T05:00:00Z"
    assert r.files == (("src/orders.py", "Pending"), ("tests/test_orders.py", "Broken"))
    assert r.next_actions == 3 and r.warnings == ()
    assert any("broken" in x for x in r.repairs)


def test_parse_repairs_slips_and_reports_gaps() -> None:
    sloppy = (
        "# state\nTimestamp: now  Phase: debug\n"
        "### objective\nDo it.\n"
        "## 6) next steps\n1. one 2. two\n"
        "## 3. Changed\n| a.py | done-ish | x |\n"
    )
    r = h.parse_context(sloppy)
    assert not r.ok and set(r.missing) == {2, 4, 5}
    assert r.phase == "Debugging" and any("debug" in x for x in r.repairs)
    assert any("number only" in x for x in r.repairs)
    assert r.next_actions == 2 and any("3 to 5" in w for w in r.warnings)
    assert r.files == (("a.py", "done-ish"),)
    empty = h.parse_context("")
    assert not empty.ok and empty.missing == (1, 2, 3, 4, 5, 6)
    big = h.parse_context(GUIDE_EXAMPLE + "x" * 9000)
    assert any("bytes" in w for w in big.warnings)


def test_the_hand_off_line_stays_one_line() -> None:
    # CrossHair in CI: a phase with a newline in it split the line the state file carries.
    report = h.ContextReport(True, "a\nb", "", (), (), (), 0, (), (), 0)
    line = h.describe_context(report, "x\ry.md", "\x00now\n")
    assert line.startswith("hand-off file") and "\n" not in line and "\r" not in line
    assert "phase a b," in line


def test_prompts_carry_the_schema_and_the_validation_step() -> None:
    cli = h.extraction_prompt("/s/claude_context.md", web=False)
    assert "/s/claude_context.md" in cli and "## 5. Active Blockers & Errors" in cli
    web = h.extraction_prompt("/s/claude_context.md", web=True)
    assert "bee handoff import" in web and "/s/" not in web
    hyd = h.hydration_prompt("/s/c.md", "/s/PROJECT_STATE.md", "/s/EXTRACT.md")
    assert "Filesystem validation" in hyd and "Blocker verification" in hyd
    assert hyd.index("Filesystem validation") < hyd.index("**Execute**")


def gate(**over: object) -> tuple[str, str]:
    base: dict[str, object] = dict(
        gate_on=True,
        stop_hook_active=False,
        head="h2",
        context_head="h1",
        context_exists=True,
        asked_head="",
        used_pct=-1,
        threshold=90,
        asked_threshold=False,
        extract_path="/s/EXTRACT.md",
    )
    base.update(over)
    return h.gate_feedback(**base)  # type: ignore[arg-type]


def test_stop_gate() -> None:
    assert gate()[1] == "commit"
    assert gate(gate_on=False) == ("", "")
    assert gate(stop_hook_active=True) == ("", "")
    assert gate(context_head="h2") == ("", "")
    assert gate(asked_head="h2") == ("", "")  # once per commit
    assert gate(used_pct=91)[1] == "threshold"
    assert gate(used_pct=91, asked_threshold=True)[1] == "commit"
    assert "does not exist" in gate(context_exists=False, context_head="")[0]


def test_status_line() -> None:
    line = h.statusline_text("b", 92, "15:45", 40, 30, 90, color=False)
    assert line.startswith("bee:b · 5h 92% until 15:45 · 7d 40% · ctx 30%")
    assert "bee continue" in line
    assert "/compact" in h.statusline_text("b", 10, "", -1, 65, 90, color=False)
    assert h.statusline_text("", -1, "", -1, -1, 90, color=False) == "bee:unnamed"
    assert "\033[" in h.statusline_text("b", 92, "", -1, -1, 90, color=True)


def test_status_line_survives_odd_numbers(tmp_path: Path) -> None:
    # CrossHair: a percentage too long to print within the bound broke the one short line.
    env = {"NO_COLOR": "1", "BEETHOVEN_STATE_DIR": str(tmp_path / "s"), "BEE_THRESHOLD": "500"}
    payload: dict[str, object] = {
        "rate_limits": {
            "five_hour": {"used_percentage": 1e300, "resets_at": 10**30},
            "seven_day": {"used_percentage": "nan"},
        },
        "context_window": {"used_percentage": "not a number"},
        "cwd": str(tmp_path),
    }
    line = h._statusline(payload, env)
    assert line.startswith("bee:unnamed · 5h 100% · ") and len(line) <= 400
    assert "7d" not in line and "ctx" not in line and "bee continue" in line


# --- the hooks, end to end in a scratch repository ---------------------------------------------


def run_hook(name: str, payload: dict[str, object]) -> str:
    out = io.StringIO()
    assert h.main([name], io.StringIO(json.dumps(payload)), out) == 0
    return out.getvalue()


@pytest.fixture
def env(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("BEE_ACCOUNT", "second")
    monkeypatch.setenv("BEE_REGISTRY_DIR", str(tmp_path / "registry"))
    for name in ("BEETHOVEN_STATE_DIR", "BEETHOVEN_RUN_ROOT", "BEETHOVEN_HANDOFF",
                 "BEETHOVEN_HANDOFF_GATE", "NO_COLOR"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    return project / ".claude" / "state"


def test_session_start_records_and_injects(env: Path, project: Path) -> None:
    payload = {"session_id": "s1", "transcript_path": "/claude/projects/-work/s1.jsonl",
               "source": "startup", "cwd": str(project)}  # fmt: skip
    out = json.loads(run_hook("session-start", payload))
    note = out["hookSpecificOutput"]["additionalContext"]
    assert "claude_context.md" in note and "PROJECT STATE" not in note
    session = json.loads((env / "session.json").read_text())
    assert session["transcript_path"] == "/claude/projects/-work/s1.jsonl"
    assert session["account"] == "second" and len(session["head_at_start"]) == 40
    assert oct((env / "session.json").stat().st_mode & 0o777) == "0o600"
    assert (env / ".gitignore").read_text().endswith("*\n")
    assert (env / "EXTRACT.md").exists() and (env / "HYDRATE.md").exists()
    resumed = json.loads(run_hook("session-start", {**payload, "source": "compact"}))
    assert "PROJECT STATE" in resumed["hookSpecificOutput"]["additionalContext"]
    assert len(resumed["hookSpecificOutput"]["additionalContext"]) <= h.INJECT_LIMIT


def test_hand_off_start_points_at_the_hydration_steps(
    env: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEETHOVEN_HANDOFF", "1")
    out = json.loads(run_hook("session-start", {"session_id": "s2", "source": "startup"}))
    text = out["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("Continuing from a hand-off") and "HYDRATE.md" in text


def test_post_tool_use_keeps_the_state_file_current(env: Path, project: Path) -> None:
    run_hook("post-tool-use", {"tool_name": "Write", "tool_input": {"file_path": "x"}})
    first = (env / "PROJECT_STATE.md").read_text()
    assert "Uncommitted changes (0)" in first
    (project / "b.py").write_text("y = 2\n")
    run_hook("post-tool-use", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert "?? b.py" in (env / "PROJECT_STATE.md").read_text()


def test_a_written_hand_off_file_is_redacted_and_recorded(env: Path, project: Path) -> None:
    run_hook("session-start", {"session_id": "s1", "source": "startup"})
    target = env / "claude_context.md"
    target.write_text(GUIDE_EXAMPLE + "\nthe key was ghp_" + "Z" * 36 + "\n")
    run_hook("post-tool-use", {"tool_name": "Write", "tool_input": {"file_path": str(target)}})
    assert "ghp_" not in target.read_text()
    record = json.loads((env / "context.json").read_text())
    assert record["ok"] and record["redacted"] == 1 and len(record["head"]) == 40
    assert "phase Debugging" in (env / "PROJECT_STATE.md").read_text()


def test_stop_gate_asks_once_per_commit(
    env: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_hook("session-start", {"session_id": "s1", "source": "startup"})
    stop = {"session_id": "s1", "stop_hook_active": False}
    assert run_hook("stop", stop) == ""  # the gate is off by default
    monkeypatch.setenv("BEETHOVEN_HANDOFF_GATE", "1")
    first = json.loads(run_hook("stop", stop))
    assert "does not exist" in first["hookSpecificOutput"]["additionalContext"]
    assert run_hook("stop", stop) == ""  # asked about this commit already
    (env / "claude_context.md").write_text(GUIDE_EXAMPLE)
    assert run_hook("stop", stop) == ""  # fresh for this commit
    commit(project, "c.py", "z = 3\n")
    again = json.loads(run_hook("stop", stop))
    assert "committed since" in again["hookSpecificOutput"]["additionalContext"]
    assert run_hook("stop", {**stop, "stop_hook_active": True}) == ""


def test_stop_gate_asks_at_the_threshold(
    env: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEETHOVEN_HANDOFF_GATE", "1")
    run_hook("session-start", {"session_id": "s1", "source": "startup"})
    (env / "claude_context.md").write_text(GUIDE_EXAMPLE)
    run_hook("stop", {"session_id": "s1"})  # record the file against this commit
    line = run_hook(
        "statusline",
        {"session_id": "s1", "workspace": {"project_dir": str(project)},
         "rate_limits": {"five_hour": {"used_percentage": 93.4, "resets_at": 1_800_000_000}}},
    )  # fmt: skip
    assert "5h 93%" in line
    out = json.loads(run_hook("stop", {"session_id": "s1"}))
    assert "93%" in out["hookSpecificOutput"]["additionalContext"]
    assert run_hook("stop", {"session_id": "s1"}) == ""


def test_usage_limit_is_recorded(env: Path, project: Path, tmp_path: Path) -> None:
    run_hook(
        "statusline",
        {"session_id": "s1", "cwd": str(project),
         "rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": 1_900_000_000},
                         "seven_day": {"used_percentage": 50, "resets_at": 1_900_500_000}},
         "context_window": {"used_percentage": 12}},
    )  # fmt: skip
    usage = json.loads((tmp_path / "registry" / "usage" / "second.json").read_text())
    assert usage["five_hour_pct"] == 100 and usage["seven_day_resets_at"] == 1_900_500_000
    out = run_hook(
        "stop-failure",
        {"session_id": "s1", "error": "rate_limit", "transcript_path": "/t/s1.jsonl",
         "error_details": "429 Too Many Requests"},
    )  # fmt: skip
    assert json.loads(out)["terminalSequence"].startswith("\033]9;Beethoven:")
    limit = json.loads((env / "limit.json").read_text())
    assert limit["account"] == "second" and limit["five_hour_resets_at"] == 1_900_000_000
    assert run_hook("stop-failure", {"session_id": "s1", "error": "overloaded"}) == ""
    assert "stop-failure error=overloaded" in (env / "handoff.log").read_text()


def test_run_root_moves_the_state(
    env: Path, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEETHOVEN_RUN_ROOT", str(tmp_path / "run"))
    run_hook("session-start", {"session_id": "s1", "source": "startup"})
    assert (tmp_path / "run" / "state" / "PROJECT_STATE.md").exists()


def test_hooks_never_break_a_session(env: Path) -> None:
    out = io.StringIO()
    assert h.main(["nonsense"], io.StringIO("{}"), out) == 0
    assert h.main(["stop"], io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""
    assert "nonsense" in (env / "hook-errors.log").read_text()


def test_install_unwraps_and_redacts(tmp_path: Path) -> None:
    pasted = "```markdown\n" + GUIDE_EXAMPLE + "token: sk-ant-abcdefghijk\n```\n"
    report, hits = h.install_context(str(tmp_path), pasted)
    assert report.ok and hits == 1
    text = (tmp_path / "claude_context.md").read_text()
    assert text.startswith("# CLAUDE STATE") and "sk-ant" not in text
    h.install_context(str(tmp_path), GUIDE_EXAMPLE)
    assert (tmp_path / "claude_context.prev.md").exists()


def test_hooks_run_by_path_as_the_image_runs_them(env: Path, project: Path) -> None:
    import subprocess
    import sys

    script = Path(h.__file__)
    done = subprocess.run(
        [sys.executable, str(script), "session-start"],
        input=json.dumps({"session_id": "s9", "source": "resume"}),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
        check=True,
    )
    assert "PROJECT STATE" in json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_limit_keeps_only_its_own_sessions_reading(env: Path, project: Path) -> None:
    """The project's usage.json may still hold the account before: its resets are not this one's."""
    env.mkdir(parents=True, exist_ok=True)
    (env / "usage.json").write_text(
        json.dumps(
            {
                "label": "first",
                "session_id": "s0",
                "five_hour_pct": 88,
                "five_hour_resets_at": 1_900_000_000,
                "seven_day_pct": 100,
                "seven_day_resets_at": 1_900_500_000,
            }
        )  # fmt: skip
    )
    run_hook("stop-failure", {"session_id": "s1", "error": "rate_limit"})
    limit = json.loads((env / "limit.json").read_text())
    assert limit["account"] == "second" and limit["five_hour_resets_at"] == 0
    assert limit["seven_day_pct"] is None and limit["seven_day_resets_at"] == 0


def test_a_limit_the_session_outlived_is_retired(env: Path, project: Path) -> None:
    run_hook("stop-failure", {"session_id": "s1", "error": "rate_limit"})
    run_hook("stop", {"session_id": "other", "cwd": str(project)})
    assert "handled" not in json.loads((env / "limit.json").read_text())
    run_hook("stop", {"session_id": "s1", "cwd": str(project)})  # the same session went on
    assert "went on" in json.loads((env / "limit.json").read_text())["handled"]


def test_stop_makes_no_state_folder_without_the_gate(tmp_path: Path, env: Path) -> None:
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    run_hook("stop", {"session_id": "s1", "cwd": str(elsewhere)})
    assert not (elsewhere / ".claude").exists()


def test_a_fenced_comment_is_not_a_heading() -> None:
    text = GUIDE_EXAMPLE.replace(
        "1. Run grep to find the serializer.",
        "1. Run grep to find the serializer:\n   ```sh\n   # reproduce the error first\n   ```",
    )
    report = h.parse_context(text)
    assert report.ok and report.next_actions == 3 and not report.repairs[1:]


def test_the_status_line_speaks_this_machines_words(
    env: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = {"five_hour": {"used_percentage": 95, "resets_at": 1_900_000_000}}
    event = {"session_id": "s1", "cwd": str(project), "rate_limits": window}
    monkeypatch.setenv("BEE_CONTINUE_HINT", "bee-grid continue")
    line = run_hook("statusline", event)
    assert "ask for the hand-off file, then exit and run bee-grid continue" in line
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "commands" / "handoff.md").write_text("x\n")
    monkeypatch.delenv("BEE_CONTINUE_HINT")
    assert "/handoff, then exit and run bee continue" in run_hook("statusline", event)


def test_the_status_line_survives_a_failed_record(
    env: Path, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x\n")
    monkeypatch.setenv("BEE_REGISTRY_DIR", str(blocker / "registry"))
    monkeypatch.setenv("NO_COLOR", "1")
    line = run_hook(
        "statusline",
        {"session_id": "s1", "cwd": str(project),
         "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": 1_900_000_000}}},
    )  # fmt: skip
    assert line.startswith("bee:second · 5h 40%")
    assert "statusline" in (env / "hook-errors.log").read_text()
