"""Shared fixtures for bee's tests: a private home, the fake claude, a scratch git project."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

FAKES = Path(__file__).resolve().parent / "fakes"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of its own: ~/.beethoven under tmp, the fake claude, no credential variables."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("BEETHOVEN_HOME", str(h / ".beethoven"))
    monkeypatch.delenv("BEE_REGISTRY_DIR", raising=False)
    monkeypatch.delenv("BEETHOVEN_STATE_DIR", raising=False)
    monkeypatch.delenv("BEETHOVEN_RUN_ROOT", raising=False)
    monkeypatch.delenv("BEE_ACCOUNT", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    for marker in (
        "CLAUDECODE",
        "BEETHOVEN_REQUEST_ID",
        "ASHLEY_REQUEST_ID",
        "SLURM_JOB_ID",
        "SLURMD_NODENAME",
    ):
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv("BEE_CLAUDE", str(FAKES / "claude"))
    monkeypatch.setenv("BEE_NOT_A_CLUSTER", "1")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    return h


def signed_in(path: str | Path, who: str) -> None:
    """Mark a directory signed in for the fake claude (its own marker, never a credential)."""
    Path(path).mkdir(parents=True, exist_ok=True)
    (Path(path) / ".fake-signed-in").write_text(who + "\n")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repository with one commit."""
    p = tmp_path / "proj"
    p.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}  # fmt: skip
    subprocess.run(["git", "init", "-q", "-b", "main", str(p)], check=True, env=env)
    (p / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(p), "add", "a.py"], check=True, env=env)
    subprocess.run(["git", "-C", str(p), "commit", "-q", "-m", "first"], check=True, env=env)
    return p


def commit(p: Path, name: str, text: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}  # fmt: skip
    (p / name).write_text(text)
    subprocess.run(["git", "-C", str(p), "add", name], check=True, env=env)
    subprocess.run(["git", "-C", str(p), "commit", "-q", "-m", f"add {name}"], check=True, env=env)
    out = subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True)  # fmt: skip
    return out.stdout.strip()
