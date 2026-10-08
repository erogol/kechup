"""Shared test fixtures: a real temp git repo per test, a CLI runner that
invokes kechup as a real subprocess (no mocks of git)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest

KECHUP_SRC = str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True, scope="session")
def _isolated_bootstrap_lock_dir(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Every test takes bootstrap locks in a private temp dir, so the
    system temp dir does not collect one lock file per test project."""
    lock_dir = tmp_path_factory.mktemp("kechup-lock-dir")
    old = os.environ.get("KECHUP_LOCK_DIR")
    os.environ["KECHUP_LOCK_DIR"] = str(lock_dir)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("KECHUP_LOCK_DIR", None)
        else:
            os.environ["KECHUP_LOCK_DIR"] = old


@dataclass
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    json: dict[str, Any] | None


def run_kechup(
    args: list[str],
    cwd: Path,
    agent: str | None = None,
    as_json: bool = True,
    extra_env: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> CliResult:
    env = dict(os.environ)
    env["PYTHONPATH"] = KECHUP_SRC
    env.pop("KECHUP_AGENT", None)
    if agent is not None:
        env["KECHUP_AGENT"] = agent
    if extra_env:
        env.update(extra_env)
    full_args = [sys.executable, "-m", "kechup"]
    if as_json:
        full_args.append("--json")
    full_args.extend(args)
    result = subprocess.run(
        full_args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    parsed = None
    if as_json and result.stdout.strip():
        import json

        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            parsed = None
    return CliResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        json=parsed,
    )


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def git_as(
    cwd: Path, name: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command as a given human/agent identity, independent of
    this machine's global git config."""
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = f"{name}@test.local"
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = f"{name}@test.local"
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


@pytest.fixture
def tmp_dir() -> Path:
    with tempfile.TemporaryDirectory(prefix="kechup-test-") as tmp:
        yield Path(tmp)


@pytest.fixture
def new_project(tmp_dir: Path) -> Path:
    """A fresh `kechup new` project, agent `owner` as the project owner."""
    target = tmp_dir / "proj"
    result = run_kechup(
        ["new", str(target), "--goal", "a test project", "--owner", "owner"],
        cwd=tmp_dir,
        agent="owner",
    )
    assert result.exit_code == 0, result.stderr
    git_as(target, "owner", "config", "user.name", "owner")
    git_as(target, "owner", "config", "user.email", "owner@test.local")
    return target
