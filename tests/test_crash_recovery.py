"""Crash-residue automatic recovery tests (CRASH_RECOVERY_SPEC v2).
Real processes, real SIGKILL, no mocks. Tests 1, 9, 10 are red on the
base branch; tests 2-8 guard fail-closed refusal on the candidate."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from conftest import KECHUP_SRC, git, git_as, run_kechup

GUARDED = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]
HELPER = Path(__file__).parent / "helpers" / "crash_claim.py"


def _add_task(project: Path, title: str) -> str:
    result = run_kechup(["add", "task", title], cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def _git_dir(project: Path) -> Path:
    out = git(project, "rev-parse", "--git-dir").stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = project / path
    return path.resolve()


def _journal_path(project: Path) -> Path:
    return _git_dir(project) / "kechup-journal.json"


def _spawn_crash_claim(project: Path, task_id: str, kill_point: str
                       ) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = KECHUP_SRC
    env["KILL_POINT"] = kill_point
    return subprocess.Popen(
        [sys.executable, str(HELPER), str(project), task_id],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached before timeout")


def _guarded_dirty(project: Path) -> bool:
    out = git(project, "status", "--porcelain", "--", *GUARDED).stdout
    return bool(out.strip())


def _crash_and_kill(project: Path, task_id: str, kill_point: str) -> None:
    proc = _spawn_crash_claim(project, task_id, kill_point)
    journal = _journal_path(project)
    tmp = project / ".kechup" / "tasks.json.tmp"
    if kill_point == "before_write":
        # Candidate: the journal appears first. Base: the .tmp write file
        # appears first. Either proves the process reached the write phase.
        _wait_until(lambda: journal.exists() or tmp.exists())
    elif kill_point == "before_commit":
        _wait_until(lambda: _guarded_dirty(project))
    else:  # after_commit
        _wait_until(
            lambda: git(project, "log", "--format=%s", "-1").stdout.strip() == f"Claim {task_id}"
        )
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)


def _show(project: Path, task_id: str) -> dict:
    result = run_kechup(["show", task_id], cwd=project, agent="x")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]


def _log_subjects_authors(project: Path) -> list[tuple[str, str]]:
    out = git(project, "log", "--format=%an%x1f%s").stdout
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        author, subject = line.split("\x1f", 1)
        rows.append((author, subject))
    return rows


# --- Test 1: recover a complete owned interrupted transaction ---

def test_kill_before_commit_next_write_recovers_automatically(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    assert _guarded_dirty(new_project), "residue must be present after the kill"
    assert _journal_path(new_project).exists()

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr

    assert not _journal_path(new_project).exists(), "journal must be cleared"
    assert not _guarded_dirty(new_project), "tree must be clean"

    rows = _log_subjects_authors(new_project)
    assert ("crash-agent", f"Claim {t1}") in rows, "recovery must commit the interrupted claim"
    assert ("agent-b", f"Claim {t2}") in rows, "the new command must commit its own write"
    # git log lists newest first: the recovered claim is older than the
    # command that ran the recovery.
    assert rows.index(("agent-b", f"Claim {t2}")) < rows.index(("crash-agent", f"Claim {t1}"))

    assert _show(new_project, t1)["owner"] == "crash-agent"
    assert _show(new_project, t2)["owner"] == "agent-b"


# --- Test 2: interrupted before any file write: no replay, no residue ---

def test_kill_before_file_write_journal_discarded_no_replay(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_write")
    assert not _guarded_dirty(new_project), "no guarded file may be touched"
    assert _journal_path(new_project).exists()

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr

    assert not _journal_path(new_project).exists()
    assert ("crash-agent", f"Claim {t1}") not in _log_subjects_authors(new_project), (
        "a never-started transaction must not be replayed"
    )
    assert _show(new_project, t1)["state"] == "todo"
    assert _show(new_project, t2)["owner"] == "agent-b"


# --- Test 3: human edit after the crash keeps the refusal ---

def test_human_edit_after_crash_still_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    tasks_json = new_project / ".kechup" / "tasks.json"
    tasks_json.write_text(tasks_json.read_text().replace('"priority": 5', '"priority": 9'))

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"
    assert _journal_path(new_project).exists(), "evidence must be kept on refusal"


# --- Test 5: invalid journal JSON keeps the refusal ---

def test_invalid_journal_json_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    _journal_path(new_project).write_text("{ not json", encoding="utf-8")

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


def _read_journal(project: Path) -> dict:
    return json.loads(_journal_path(project).read_text(encoding="utf-8"))


def _write_journal(project: Path, data: dict) -> None:
    _journal_path(project).write_text(json.dumps(data), encoding="utf-8")


# --- Test 5b: schema violations keep the refusal ---

def test_journal_schema_violations_refuse(new_project: Path) -> None:
    # Each mutation needs a fresh interrupted claim: after `repair`,
    # the previous task is already claimed.
    def _refuses_with(mutate) -> None:
        t1 = _add_task(new_project, f"one {_refuses_with.calls}")
        t2 = _add_task(new_project, f"two {_refuses_with.calls}")
        _crash_and_kill(new_project, t1, "before_commit")
        data = _read_journal(new_project)
        mutate(data)
        _write_journal(new_project, data)
        result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
        assert result.exit_code == 3, result.stderr
        assert result.json["error"]["code"] == "crash_residue"
        repair = run_kechup(["repair"], cwd=new_project, agent="owner")
        assert repair.exit_code == 0, repair.stderr
        _refuses_with.calls += 1

    _refuses_with.calls = 0
    _refuses_with(lambda d: d.update(version=2))
    _refuses_with(lambda d: d.update(extra_key="x"))
    _refuses_with(lambda d: d.pop("base_head"))
    _refuses_with(lambda d: d.update(paths={}))
    _refuses_with(lambda d: d.update(agent=""))
    _refuses_with(lambda d: d.update(paths={"notes/foreign.txt": "x"}))


# --- Test 6: moved HEAD keeps the refusal ---

def test_journal_base_head_mismatch_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    (new_project / "foreign.txt").write_text("foreign work", encoding="utf-8")
    git_as(new_project, "stranger", "add", "foreign.txt")
    # Pathspec commit: HEAD moves, but the staged guarded files stay
    # dirty and staged. A whole-index commit would swallow them and
    # give the clean-tree state instead.
    git_as(new_project, "stranger", "commit", "-m", "Foreign work", "--", "foreign.txt")

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


# --- Test 7: a dirty guarded path outside the journal keeps the refusal ---

def test_dirty_guarded_path_not_journaled_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    config_json = new_project / ".kechup" / "config.json"
    config_json.write_text(
        config_json.read_text().replace('"owner_id": "owner"', '"owner_id": "hacker"'),
        encoding="utf-8",
    )

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


def test_journal_path_dropped_from_paths_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    data = _read_journal(new_project)
    data["paths"].pop("keche.md")
    _write_journal(new_project, data)

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


# --- Test 8: foreign staged index content keeps the refusal ---

def test_foreign_staged_content_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    tasks_json = new_project / ".kechup" / "tasks.json"
    intended = _read_journal(new_project)["paths"][".kechup/tasks.json"]
    tasks_json.write_text(intended.replace('"priority": 5', '"priority": 9'), encoding="utf-8")
    git_as(new_project, "stranger", "add", ".kechup/tasks.json")
    # on-disk bytes match the journal again; only the staged entry is foreign
    tasks_json.write_text(intended, encoding="utf-8")

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


# --- Test 9: crash after the commit, before the journal clear ---

def test_kill_after_commit_journal_cleared_next_write_succeeds(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "after_commit")
    assert not _guarded_dirty(new_project), "the claim already landed"
    assert _journal_path(new_project).exists()

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr
    assert not _journal_path(new_project).exists()
    assert _show(new_project, t1)["owner"] == "crash-agent"
    assert _show(new_project, t2)["owner"] == "agent-b"


# --- Test 10: live writer keeps the lock; after its death recovery runs ---

def test_live_writer_lock_timeout_then_recovery_after_kill(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    proc = _spawn_crash_claim(new_project, t1, "before_commit")
    _wait_until(lambda: _journal_path(new_project).exists() and _guarded_dirty(new_project))

    blocked = run_kechup(["claim", t2], cwd=new_project, agent="agent-b", timeout=30.0)
    assert blocked.exit_code == 3
    assert blocked.json["error"]["code"] == "lock_timeout"

    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr
    assert _show(new_project, t1)["owner"] == "crash-agent"
    assert _show(new_project, t2)["owner"] == "agent-b"
    assert not _guarded_dirty(new_project)
    assert not _journal_path(new_project).exists()


# --- Test 11: manual repair still works and clears the journal ---
def test_manual_repair_clears_journal(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    assert _journal_path(new_project).exists()

    repair = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert repair.exit_code == 0, repair.stderr
    assert not _journal_path(new_project).exists(), "repair must clear the journal"

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr


# --- Advisor review: adversarial worktree and index states ---

def test_symlink_residue_stays_refused(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    tasks_json = new_project / ".kechup" / "tasks.json"
    blob = tasks_json.read_bytes()
    # A symlink whose target holds the intended bytes: the bytes check
    # alone would pass, the file-type check must refuse it.
    target = new_project / ".kechup" / "tasks.json.target"
    target.write_bytes(blob)
    tasks_json.unlink()
    tasks_json.symlink_to(target)

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"
    assert tasks_json.is_symlink(), "refusal must not touch the residue"


def test_exec_bit_residue_stays_refused(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    tasks_json = new_project / ".kechup" / "tasks.json"
    tasks_json.chmod(0o755)

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


def test_staged_deletion_residue_stays_refused(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    # A foreign `git rm --cached` removes the index entry while the
    # worktree file keeps the intended bytes.
    git(new_project, "rm", "--cached", "--", ".kechup/tasks.json")

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"


def test_single_conflict_stage_entry_residue_stays_refused(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    # A single nonzero-stage index entry (an add/delete conflict) that
    # holds the intended bytes: `ls-files --stage` shows one line with
    # stage 3. The stage check must refuse it, and the recovery must
    # not run `git add` over the conflict.
    tasks_json = new_project / ".kechup" / "tasks.json"
    sha = git(new_project, "hash-object", "-w", "--", str(tasks_json)).stdout.strip()
    head_before = git(new_project, "rev-parse", "HEAD").stdout.strip()
    index_before = git(new_project, "ls-files", "--stage", "--", ".kechup/tasks.json").stdout
    # Feed the entry through stdin: the command runs in the project cwd,
    # so the entry file stays inside the project. Format:
    # <mode> SP <type> SP <sha> SP <stage> TAB <path>. Remove the
    # normal stage-0 entry first: the single-nonzero-stage case is a
    # real add/delete conflict state.
    git(new_project, "update-index", "--force-remove", "--", ".kechup/tasks.json")
    entry_path = new_project / ".kechup" / "conflict-entry"
    entry_path.write_text(
        f"100644 blob {sha} 3\t.kechup/tasks.json\n", encoding="utf-8"
    )
    with open(entry_path, "rb") as fh:
        proc = subprocess.run(
            ["git", "update-index", "--index-info"],
            cwd=new_project, stdin=fh,
        )
    entry_path.unlink()
    assert proc.returncode == 0, "update-index --index-info must succeed"
    # Exactly one index line with stage 3 must remain.
    stage_lines = git(
        new_project, "ls-files", "--stage", "--", ".kechup/tasks.json"
    ).stdout
    assert stage_lines == f"100644 {sha} 3\t.kechup/tasks.json\n"

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"
    # The refusal must leave HEAD, the conflict index, and the journal
    # untouched.
    assert git(new_project, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (
        git(new_project, "ls-files", "--stage", "--", ".kechup/tasks.json").stdout
        == f"100644 {sha} 3\t.kechup/tasks.json\n"
    )
    assert _journal_path(new_project).exists()
    assert index_before  # sanity: a stage-0 entry existed before


def test_journal_version_true_and_one_float_refuse(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    path = _journal_path(new_project)
    for bad in (True, 1.0):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = bad
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
        assert result.exit_code == 3
        assert result.json["error"]["code"] == "crash_residue"


def test_journal_duplicate_key_refuses(new_project: Path) -> None:
    t1 = _add_task(new_project, "one")
    t2 = _add_task(new_project, "two")

    _crash_and_kill(new_project, t1, "before_commit")
    path = _journal_path(new_project)
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = sorted(data)
    dup_key = keys[0]
    # Re-serialize with the key twice. json.dumps of a dict cannot
    # produce duplicates, so build the text by hand from valid parts.
    lines = []
    for key, value in data.items():
        if key == dup_key:
            lines.append(json.dumps(key) + ": " + json.dumps(value))
            lines.append(json.dumps(key) + ": " + json.dumps(value))
        else:
            lines.append(json.dumps(key) + ": " + json.dumps(value))
    path.write_text("{\n" + ",\n".join(f"  {line}" for line in lines) + "\n}\n", encoding="utf-8")

    result = run_kechup(["claim", t2], cwd=new_project, agent="agent-b")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "crash_residue"