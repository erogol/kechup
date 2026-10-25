"""Real CLI evidence for the crash-recovery change (not a pytest test).

Scenario A (interruption): run a real `kechup claim`, SIGKILL it between
the guarded file write and the commit, then run a second claim. The second
claim must recover the interrupted transaction automatically.

Scenario B (concurrency): run a real `kechup claim`, and while its writer
lock is held, run a second claim. The second claim must fail with
lock_timeout. After the writer dies, a third claim must recover.

Scenario C (refusal): interrupt a claim, corrupt the on-disk tasks.json,
then run a claim. It must refuse with crash_residue.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "tests"))
from conftest import KECHUP_SRC, git, run_kechup  # noqa: E402

HELPER = WORKTREE / "tests" / "helpers" / "crash_claim.py"
GUARDED = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]


def evidence(text: str) -> None:
    print(text, flush=True)


def git_dir(project: Path) -> Path:
    out = git(project, "rev-parse", "--absolute-git-dir").stdout.strip()
    return Path(out)


def journal_path(project: Path) -> Path:
    return git_dir(project) / "kechup-journal.json"


def guarded_dirty(project: Path) -> bool:
    return bool(git(project, "status", "--porcelain", "--", *GUARDED).stdout.strip())


def spawn_claim(project: Path, task_id: str, kill_point: str
                ) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = KECHUP_SRC
    env["KILL_POINT"] = kill_point
    return subprocess.Popen(
        [sys.executable, str(HELPER), str(project), task_id],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def wait_until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached before timeout")


def scenario_a(tmp: Path) -> bool:
    evidence("=== Scenario A: interruption between write and commit ===")
    project = tmp / "a-proj"
    run_kechup(["new", str(project), "--goal", "evidence a", "--owner", "owner"],
               cwd=tmp, agent="owner")
    t1 = run_kechup(["add", "task", "one"], cwd=project, agent="owner").json["data"]["id"]
    t2 = run_kechup(["add", "task", "two"], cwd=project, agent="owner").json["data"]["id"]

    proc = spawn_claim(project, t1, "before_commit")
    wait_until(lambda: guarded_dirty(project))
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    evidence(f"SIGKILL landed before commit; dirty guarded files: {guarded_dirty(project)}")
    evidence(f"journal present: {journal_path(project).exists()}")

    result = run_kechup(["claim", t2], cwd=project, agent="agent-b")
    evidence(f"next claim exit_code={result.exit_code}")
    log = git(project, "log", "--format=%an %s").stdout
    ok = (result.exit_code == 0
          and "crash-agent Claim" in " ".join(log.splitlines())
          and "agent-b Claim" in " ".join(log.splitlines())
          and not guarded_dirty(project)
          and not journal_path(project).exists())
    evidence(f"HEAD log: {log.strip().splitlines()}")
    evidence(f"Scenario A result: {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_b(tmp: Path) -> bool:
    evidence("=== Scenario B: live writer keeps the lock ===")
    project = tmp / "b-proj"
    run_kechup(["new", str(project), "--goal", "evidence b", "--owner", "owner"],
               cwd=tmp, agent="owner")
    t1 = run_kechup(["add", "task", "one"], cwd=project, agent="owner").json["data"]["id"]
    t2 = run_kechup(["add", "task", "two"], cwd=project, agent="owner").json["data"]["id"]

    proc = spawn_claim(project, t1, "before_commit")
    wait_until(lambda: journal_path(project).exists() and guarded_dirty(project))
    blocked = run_kechup(["claim", t2], cwd=project, agent="agent-b", timeout=30.0)
    evidence(f"concurrent claim exit_code={blocked.exit_code}, "
             f"error={blocked.json['error']['code'] if blocked.json else blocked.stderr}")
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    result = run_kechup(["claim", t2], cwd=project, agent="agent-b")
    ok = (blocked.exit_code == 3
          and blocked.json["error"]["code"] == "lock_timeout"
          and result.exit_code == 0
          and not guarded_dirty(project)
          and not journal_path(project).exists())
    evidence(f"post-kill claim exit_code={result.exit_code}, "
             f"dirty={guarded_dirty(project)}, journal={journal_path(project).exists()}")
    evidence(f"Scenario B result: {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_c(tmp: Path) -> bool:
    evidence("=== Scenario C: corrupted residue stays refused ===")
    project = tmp / "c-proj"
    run_kechup(["new", str(project), "--goal", "evidence c", "--owner", "owner"],
               cwd=tmp, agent="owner")
    t1 = run_kechup(["add", "task", "one"], cwd=project, agent="owner").json["data"]["id"]
    t2 = run_kechup(["add", "task", "two"], cwd=project, agent="owner").json["data"]["id"]

    proc = spawn_claim(project, t1, "before_commit")
    wait_until(lambda: guarded_dirty(project))
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    tasks_json = project / ".kechup" / "tasks.json"
    # Keep the file a valid project file. Change one value so the
    # on-disk bytes mismatch the journal: the proof fails, the
    # refusal must hold.
    intended = json.loads(journal_path(project).read_text(encoding="utf-8"))[
        "paths"
    ][ ".kechup/tasks.json" ]
    tasks_json.write_text(
        intended.replace('"priority": 5', '"priority": 9'), encoding="utf-8"
    )

    result = run_kechup(["claim", t2], cwd=project, agent="agent-b")
    ok = (result.exit_code == 3
          and result.json["error"]["code"] == "crash_residue"
          and journal_path(project).exists())
    evidence(f"claim on corrupted residue exit_code={result.exit_code}, "
             f"error={result.json['error']['code']}")
    repair = run_kechup(["repair"], cwd=project, agent="owner")
    evidence(f"manual repair exit_code={repair.exit_code}")
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kechup-evidence-") as tmp_raw:
        tmp = Path(tmp_raw)
        results = [scenario_a(tmp), scenario_b(tmp), scenario_c(tmp)]
    evidence(f"=== OVERALL: {results.count(True)}/3 scenarios PASS ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())