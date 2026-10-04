"""The shell pieces: bee.sh's switch, hook.sh's dispatch, and bee-grid.sh's container calls.

bee-grid.sh runs against a stand-in apptainer that records each call, so what is checked here is
what the script asks of the container: one sign-in directory per container, the three isolation
flags, node-local temp, and the transcript bound read-only for a fork. What a real image does with
those calls is the runbook's probe, not this file's.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import FAKES, REPO, signed_in

BEE = REPO / "src" / "beethoven_research" / "bee"
BEE_SH = BEE / "shell" / "bee.sh"
HOOK_SH = BEE / "hook.sh"
GRID_SH = BEE / "bee-grid.sh"
PYTHON = REPO / ".pixi" / "envs" / "default" / "bin" / "python"

needs_env = pytest.mark.skipif(not PYTHON.exists(), reason="no pixi environment in this checkout")


@pytest.mark.parametrize("script", [BEE_SH, HOOK_SH, GRID_SH])
def test_scripts_parse_under_bash(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True)


def _shell_env(home: Path) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "BEE_ENGINE"}


@needs_env
def test_bee_use_switches_the_shell(home: Path) -> None:
    from beethoven_research.bee import cli

    assert cli.main(["account", "add", "work", "--no-login"]) == 0
    assert cli.main(["account", "add", "personal", "--default-location", "--no-login"]) == 0
    script = (
        f". '{BEE_SH}'\n"
        "bee-use work 2>/dev/null\n"
        'printf "%s|%s\\n" "$CLAUDE_CONFIG_DIR" "$BEE_ACCOUNT"\n'
        "bee-use personal 2>/dev/null\n"
        'printf "%s|%s\\n" "${CLAUDE_CONFIG_DIR-unset}" "$BEE_ACCOUNT"\n'
        "bee-use nobody 2>/dev/null || echo refused\n"
        "bee-which\n"
    )
    env = {**_shell_env(home), "CLAUDE_CONFIG_DIR": "/old", "ANTHROPIC_API_KEY": "left-behind"}
    script += 'printf "%s\\n" "${ANTHROPIC_API_KEY-cleared}"\n'
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=True
    )
    work = home / ".beethoven" / "accounts" / "work" / "claude"
    assert out.stdout.splitlines() == [
        f"{work}|work",
        "unset|personal",
        "refused",
        "personal (Claude Code's default place)",
        "cleared",
    ]


@needs_env
@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")
def test_bee_sh_finds_the_engine_under_zsh(home: Path) -> None:
    out = subprocess.run(
        ["zsh", "-c", f". '{BEE_SH}'; print -r -- $BEE_ENGINE"],
        capture_output=True,
        text=True,
        env=_shell_env(home),
        check=True,
    )
    assert out.stdout.strip() == str(REPO)


@needs_env
def test_hook_sh_runs_the_hooks(home: Path, project: Path) -> None:
    payload = {"hook_event_name": "SessionStart", "source": "resume", "session_id": "s1",
               "cwd": str(project), "transcript_path": "/x/s1.jsonl"}  # fmt: skip
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    out = subprocess.run(
        ["bash", str(HOOK_SH), "session-start"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "main" in context  # the branch, from git
    (project / ".claudeignore").write_text("*.log\n")
    ask = {"tool_name": "Read", "cwd": str(project),
           "tool_input": {"file_path": str(project / "x.log")}}  # fmt: skip
    out = subprocess.run(
        ["bash", str(HOOK_SH), "claudeignore"],
        input=json.dumps(ask),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_sh_steps_aside_without_the_engine(tmp_path: Path) -> None:
    copy = tmp_path / "src" / "beethoven_research" / "bee"
    copy.mkdir(parents=True)
    shutil.copy(HOOK_SH, copy / "hook.sh")
    out = subprocess.run(
        ["bash", str(copy / "hook.sh"), "stop"],
        input='{"hook_event_name": "Stop"}',
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout == "" and out.stderr == ""


# --- bee-grid.sh ---------------------------------------------------------------------------------


class Grid:
    """bee-grid.sh with the stand-in apptainer, a private home and a job id of its own."""

    def __init__(self, tmp_path: Path, home: Path) -> None:
        self.tmp = tmp_path
        self.bhome = home / ".beethoven"
        self.registry = self.bhome / "engine"
        self.image = tmp_path / "claude-verify.sif"
        self.image.write_text("not an image\n")
        self.log = tmp_path / "apptainer.log"
        self.rows = tmp_path / "rows.tsv"
        self.job = f"9{os.getpid()}{len(str(tmp_path))}"
        self.user = os.environ.get("USER") or "tester"
        self.scratch_prefix = f"/tmp/{self.user}/bee_{self.job}_"
        self.env = {
            "PATH": f"{FAKES}:{os.environ['PATH']}",
            "HOME": str(home),
            "USER": self.user,
            "BEETHOVEN_HOME": str(self.bhome),
            "BEE_IMAGE": str(self.image),
            "SLURM_JOB_ID": self.job,
            "SLURMD_NODENAME": "n1",
            "FAKE_APPTAINER_LOG": str(self.log),
            "FAKE_ROWS_FILE": str(self.rows),
        }

    def account(self, label: str) -> Path:
        path = self.bhome / "accounts" / label / "claude"
        path.mkdir(parents=True, exist_ok=True)
        with self.rows.open("a") as fh:
            fh.write(f"{label}\t{path}\t1\n")
        return path

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(GRID_SH), *args],
            capture_output=True,
            text=True,
            env={**self.env, **env},
            stdin=subprocess.DEVNULL,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [line.split("\t") for line in self.log.read_text().splitlines()]

    def after_image(self, call: list[str]) -> list[str]:
        return call[call.index(str(self.image)) + 1 :]


@pytest.fixture
def grid(tmp_path: Path, home: Path) -> Grid:
    return Grid(tmp_path, home)


def work(project: Path) -> str:
    """Where bee-grid.sh binds a project: a path of its own under /work."""
    return f"/work/{project.name}-{hashlib.sha256(str(project).encode()).hexdigest()[:8]}"


def scratch(call: list[str]) -> str:
    return next(x for x in binds(call) if x.endswith(":/tmp")).rsplit(":", 1)[0]


def binds(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--bind"]


def envs(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--env"]


def test_grid_refuses_outside_a_job(grid: Grid) -> None:
    out = grid.run("list", SLURM_JOB_ID="")
    assert out.returncode == 1 and "compute node" in out.stderr
    # An salloc shell: a job id, but no step on a compute node.
    out = grid.run("list", SLURMD_NODENAME="")
    assert out.returncode == 1 and "compute node" in out.stderr
    assert grid.calls() == []


def test_grid_refuses_inside_a_claude_code_session(grid: Grid, project: Path) -> None:
    b = grid.account("b")
    for sub in (("run", "b"), ("continue",), ("add", "x"), ("login", "b")):
        out = grid.run(
            *sub, "--project", str(project), CLAUDECODE="1", FAKE_PLAN=f"b\t{b}\trun\t\t"
        )
        assert out.returncode == 1 and "Claude Code session" in out.stderr, sub
    assert grid.calls() == []
    assert grid.run("list", CLAUDECODE="1").returncode == 0  # reading the list is fine


def test_grid_gives_every_call_its_own_temp(grid: Grid) -> None:
    assert grid.run("list").returncode == 0
    assert grid.run("list").returncode == 0
    first, second = (scratch(c) for c in grid.calls())
    assert first != second and first.startswith(grid.scratch_prefix)
    assert not Path(first).exists() and not Path(second).exists()


def test_grid_refuses_without_the_image(grid: Grid) -> None:
    out = grid.run("list", BEE_IMAGE=str(grid.tmp / "absent.sif"))
    assert out.returncode == 1 and "no image" in out.stderr


def test_grid_continue_binds_one_sign_in_per_container(grid: Grid, project: Path) -> None:
    grid.account("a")
    b = grid.account("b")
    plan = f"b\t{b}\tfile\t\tRead .claude/state/HYDRATE.md and follow it."
    out = grid.run("continue", "--project", str(project), "--", "--model", "x", FAKE_PLAN=plan)
    assert out.returncode == 0, out.stderr
    planned, status, session = grid.calls()
    for call in (planned, status, session):
        assert call[:4] == ["exec", "--containall", "--cleanenv", "--no-home"]
        assert scratch(call) == scratch(planned) and scratch(call).startswith(grid.scratch_prefix)
        signed = [x for x in binds(call) if x.endswith(":/claude")]
        assert signed == ([] if call is planned else [f"{b}:/claude"])
    planned_args = ["plan", "continue", "--project", work(project), "--strategy", "order"]
    assert grid.after_image(planned)[-6:] == planned_args
    assert f"{grid.registry}:/beethoven/engine" in binds(planned)
    assert grid.after_image(status)[-5:] == ["account", "status", "b", "--bound", "/claude"]
    # The session sees only the usage readings of the list's folder, never the list itself.
    assert f"{grid.registry}/usage:/beethoven/engine/usage" in binds(session)
    assert f"{grid.registry}:/beethoven/engine" not in binds(session)
    assert not any(x.endswith(":/engine:ro") for x in binds(session))
    assert f"{project}:{work(project)}" in binds(session)
    assert session[session.index("--pwd") + 1] == work(project)
    assert {"BEETHOVEN_HANDOFF_GATE=1", "BEETHOVEN_HANDOFF=1", "CLAUDE_CONFIG_DIR=/claude",
            "BEE_ACCOUNT=b"} <= set(envs(session))  # fmt: skip
    assert grid.after_image(session) == [
        "claude", "Read .claude/state/HYDRATE.md and follow it.", "--model", "x"
    ]  # fmt: skip
    assert not Path(scratch(session)).exists()  # node-local temp is gone with the script


def test_grid_reads_the_plan_from_its_last_line(grid: Grid, project: Path) -> None:
    b = grid.account("b")
    plan = f"a reached its usage limit; it rests until 15:00.\nb\t{b}\trun\t\t"
    out = grid.run("run", "--project", str(project), FAKE_PLAN=plan)
    assert out.returncode == 0, out.stderr
    _, status, session = grid.calls()
    assert grid.after_image(status)[-5:] == ["account", "status", "b", "--bound", "/claude"]
    assert "BEE_ACCOUNT=b" in envs(session)


def test_grid_fork_binds_the_transcript_read_only(grid: Grid, project: Path) -> None:
    a = grid.account("a")
    b = grid.account("b")
    transcript = a / "projects" / "-work" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    prompt = "Continue where you stopped; this session now runs as b."
    plan = f"b\t{b}\tfork\t{transcript}\t{prompt}\t{a}"
    out = grid.run("continue", "--fork", "--project", str(project), FAKE_PLAN=plan)
    assert out.returncode == 0, out.stderr
    planned, _, session = grid.calls()
    assert grid.after_image(planned)[-1] == "--fork"
    assert f"{transcript}:/handoff/s1.jsonl:ro" in binds(session)
    assert [x for x in binds(session) if x.endswith(":/claude")] == [f"{b}:/claude"]
    assert "BEETHOVEN_HANDOFF=1" not in envs(session)
    assert grid.after_image(session) == [
        "claude", "--resume", "/handoff/s1.jsonl", "--fork-session", prompt
    ]  # fmt: skip


def test_grid_fork_without_the_transcript_stops(grid: Grid, project: Path) -> None:
    a = grid.account("a")
    b = grid.account("b")
    plan = f"b\t{b}\tfork\t{a / 'projects' / 'w' / 'gone.jsonl'}\tx\t{a}"
    out = grid.run("continue", "--fork", "--project", str(project), FAKE_PLAN=plan)
    assert out.returncode == 1 and "not there" in out.stderr
    assert len(grid.calls()) == 2  # no session started


def test_grid_fork_binds_nothing_but_a_session_file_of_its_account(
    grid: Grid, project: Path
) -> None:
    """Checked again on the host, where links resolve: the plan's path came from a record the
    last session could write."""
    a = grid.account("a")
    b = grid.account("b")
    key = a / ".credentials.json"
    key.write_text("{}\n")
    other = grid.tmp / "elsewhere" / "s1.jsonl"
    other.parent.mkdir()
    other.write_text("{}\n")
    link = a / "projects" / "w" / "s1.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(key)
    for path in (key, other, link):
        plan = f"b\t{b}\tfork\t{path}\tx\t{a}"
        out = grid.run("continue", "--fork", "--project", str(project), FAKE_PLAN=plan)
        assert out.returncode == 1 and "not a session file" in out.stderr, path
    assert not any(grid.after_image(c)[:1] == ["claude"] for c in grid.calls())


def test_grid_run_dir_moves_the_state(grid: Grid, project: Path) -> None:
    b = grid.account("b")
    run_dir = grid.tmp / "run"
    out = grid.run("run", "b", "--project", str(project), "--run-dir", str(run_dir),
                   FAKE_PLAN=f"b\t{b}\trun\t\t")  # fmt: skip
    assert out.returncode == 0, out.stderr
    planned, _, session = grid.calls()
    planned_args = ["plan", "run", "b", "--project", work(project), "--strategy", "order"]
    assert grid.after_image(planned)[-7:] == planned_args
    for call in (planned, session):
        assert f"{run_dir}:/run_dir" in binds(call) and "BEETHOVEN_RUN_ROOT=/run_dir" in envs(call)
    assert grid.after_image(session) == ["claude"]


def test_grid_named_account_not_signed_in_stops(grid: Grid, project: Path) -> None:
    b = grid.account("b")
    out = grid.run("run", "b", "--project", str(project), FAKE_PLAN=f"b\t{b}\trun\t\t",
                   FAKE_STATUS_RC="1")  # fmt: skip
    assert out.returncode == 1 and "login b" in out.stderr
    assert len(grid.calls()) == 2


def test_grid_unnamed_account_not_signed_in_leaves_the_rotation(grid: Grid, project: Path) -> None:
    b = grid.account("b")
    out = grid.run("continue", "--project", str(project), FAKE_PLAN=f"b\t{b}\tfile\t\tx",
                   FAKE_STATUS_RC="1")  # fmt: skip
    assert out.returncode == 1 and "ten tries" in out.stderr
    disabled = [c for c in grid.calls() if grid.after_image(c)[-3:] == ["account", "disable", "b"]]
    assert disabled and not any(grid.after_image(c)[:1] == ["claude"] for c in grid.calls())


def test_grid_add_makes_a_private_directory_and_signs_in_in_the_image(grid: Grid) -> None:
    out = grid.run("add", "second")
    assert out.returncode == 0, out.stderr
    path = grid.bhome / "accounts" / "second" / "claude"
    assert path.is_dir() and oct(path.stat().st_mode & 0o777) == "0o700"
    added = [c for c in grid.calls() if "add" in grid.after_image(c)]
    assert grid.after_image(added[0])[-6:] == [
        "add", "second", "--path", str(path), "--no-login", "--no-create"
    ]  # fmt: skip
    login = grid.calls()[-1]
    assert grid.after_image(login)[-4:] == ["login", "second", "--bound", "/claude"]
    assert [x for x in binds(login) if x.endswith(":/claude")] == [f"{path}:/claude"]
    assert {"CLAUDE_CONFIG_DIR=/claude", "BEE_ACCOUNT=second"} <= set(envs(login))


def test_grid_add_main_links_the_launchers_sign_in(grid: Grid) -> None:
    launcher = grid.bhome / "claude"
    launcher.mkdir(parents=True)
    signed_in(launcher, "main")
    out = grid.run("add", "main")
    assert out.returncode == 0, out.stderr
    link = grid.bhome / "accounts" / "main" / "claude"
    assert link.is_symlink() and link.resolve() == launcher.resolve()
    assert (launcher / ".fake-signed-in").exists()  # nothing inside was touched


def test_grid_refuses_a_bad_label(grid: Grid) -> None:
    for bad in ("Bad", "-x", "a/b", "a" * 33):
        out = grid.run("add", bad)
        assert out.returncode == 1 and "label" in out.stderr
    assert grid.calls() == []


def test_grid_list_edits_need_no_sign_in(grid: Grid) -> None:
    grid.account("b")
    assert grid.run("account", "disable", "b").returncode == 0
    call = grid.calls()[-1]
    assert grid.after_image(call)[-3:] == ["account", "disable", "b"]
    assert not any(x.endswith(":/claude") for x in binds(call))
    out = grid.run("account", "add", "x")
    assert out.returncode == 1 and "use add" in out.stderr


def test_grid_handoff_import_maps_the_file_into_the_project(grid: Grid, project: Path) -> None:
    (project / "notes").mkdir()
    (project / "notes" / "ctx.md").write_text("x\n")
    out = grid.run(
        "handoff", "import", str(project / "notes" / "ctx.md"), "--project", str(project)
    )
    assert out.returncode == 0, out.stderr
    call = grid.calls()[-1]
    assert f"{project}:{work(project)}" in binds(call)
    assert grid.after_image(call)[-5:] == [
        "handoff", "import", f"{work(project)}/notes/ctx.md", "--project", work(project)
    ]  # fmt: skip
    outside = grid.tmp / "elsewhere.md"
    outside.write_text("x\n")
    out = grid.run("handoff", "import", str(outside), "--project", str(project))
    assert out.returncode == 1 and "inside the project" in out.stderr
