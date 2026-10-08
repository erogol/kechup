"""Concurrency tests (spec section 10, "Concurrency"): real separate
processes, no mocks."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from conftest import KECHUP_SRC, git, run_kechup


def _add_task(project: Path, title: str) -> str:
    result = run_kechup(["add", "task", title], cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def _spawn_claim(project: Path, task_id: str, agent: str) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = KECHUP_SRC
    env["KECHUP_AGENT"] = agent
    return subprocess.Popen(
        [sys.executable, "-m", "kechup", "--json", "claim", task_id],
        cwd=str(project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_processes_racing_claim_only_one_wins(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    proc_a = _spawn_claim(new_project, t1, "agent-a")
    proc_b = _spawn_claim(new_project, t1, "agent-b")
    out_a, err_a = proc_a.communicate(timeout=20)
    out_b, err_b = proc_b.communicate(timeout=20)

    codes = {proc_a.returncode, proc_b.returncode}
    # The winner gets 0; the loser gets 2 (already claimed) or 3 (lock
    # timeout), never 0 twice.
    assert 0 in codes
    assert codes <= {0, 2, 3}
    assert not (proc_a.returncode == 0 and proc_b.returncode == 0)

    result = run_kechup(["show", t1], cwd=new_project, agent="x")
    assert result.json["data"]["owner"] in ("agent-a", "agent-b")


def test_two_new_adopt_same_path_bootstrap_lock_serializes(tmp_dir: Path) -> None:
    target = tmp_dir / "racer"

    def _spawn_new(agent: str) -> subprocess.Popen[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = KECHUP_SRC
        env["KECHUP_AGENT"] = agent
        return subprocess.Popen(
            [
                sys.executable, "-m", "kechup", "--json", "new", str(target),
                "--goal", "race goal",
            ],
            cwd=str(tmp_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    proc_a = _spawn_new("agent-a")
    proc_b = _spawn_new("agent-b")
    out_a, _ = proc_a.communicate(timeout=20)
    out_b, _ = proc_b.communicate(timeout=20)

    # Exactly one creates the project; the other refuses with "exists"
    # (it started after the first one had already made the folder), or,
    # if both started before either created the folder, both may succeed
    # only if one recovers the other's bootstrap — but the folder and a
    # single Kechup-Init commit must exist either way.
    assert (target / ".kechup" / "tasks.json").exists()
    init_log = git(target, "log", "--grep=^Kechup-Init: 1$", "--format=%H")
    init_commits = [line for line in init_log.stdout.splitlines() if line.strip()]
    assert len(init_commits) == 1
    assert proc_a.returncode == 0 or proc_b.returncode == 0


def test_kill_midwrite_real_file_untouched(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    tasks_json_path = new_project / ".kechup" / "tasks.json"
    before = tasks_json_path.read_text()

    helper = Path(__file__).parent / "helpers" / "slow_claim.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = KECHUP_SRC
    proc = subprocess.Popen(
        [sys.executable, str(helper), str(new_project), t1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    after = tasks_json_path.read_text()
    # The real file is untouched by a kill mid-write (section 6). A
    # leftover .tmp file is expected debris, not a correctness failure.
    assert after == before


def test_kill_between_write_and_commit_next_write_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    tasks_json_path = new_project / ".kechup" / "tasks.json"
    content = tasks_json_path.read_text()
    tasks_json_path.write_text(content.replace('"priority": 5', '"priority": 9'))

    result = run_kechup(["claim", t1], cwd=new_project, agent="a")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"

    repair_result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert repair_result.exit_code == 0, repair_result.stderr

    retry = run_kechup(["claim", t1], cwd=new_project, agent="a")
    assert retry.exit_code == 0, retry.stderr


def test_bootstrap_lock_dir_env_override(tmp_dir: Path) -> None:
    # KECHUP_LOCK_DIR moves the bootstrap lock out of the system temp
    # dir. The lock file itself stays on disk (never deleted in use).
    lock_dir = tmp_dir / "locks"
    lock_dir.mkdir()
    target = tmp_dir / "proj"
    result = run_kechup(
        ["new", str(target), "--goal", "g"],
        cwd=tmp_dir,
        agent="owner",
        extra_env={"KECHUP_LOCK_DIR": str(lock_dir)},
    )
    assert result.exit_code == 0, result.stderr
    assert list(lock_dir.glob("*.lock"))
