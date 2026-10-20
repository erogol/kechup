"""Tests for `new`, `adopt`, `repair` (spec section 10, "Per command")."""

from __future__ import annotations

import json
from pathlib import Path

import fcntl
import os

from conftest import git, git_as, run_kechup


def test_new_success(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    result = run_kechup(
        ["new", str(target), "--goal", "goal text", "--owner", "owner"],
        cwd=tmp_dir,
        agent="owner",
    )
    assert result.exit_code == 0, result.stderr
    assert (target / "keche.md").exists()
    assert (target / ".kechup" / "tasks.json").exists()
    assert (target / ".kechup" / "config.json").exists()
    assert (target / ".git").exists()


def test_new_name_collision(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "unrelated.txt").write_text("hi")
    result = run_kechup(
        ["new", str(target), "--goal", "goal text"], cwd=tmp_dir, agent="owner"
    )
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "exists"


def test_new_nested_repo_refusal(tmp_dir: Path) -> None:
    # The target folder must not already exist (else step 1's "exists"
    # check fires first), but the outer repo must still track a path
    # inside it: commit a file there, then remove the folder without
    # telling git, leaving it tracked in the outer repo's history.
    outer = tmp_dir / "outer"
    outer.mkdir()
    git(outer, "init", "-q", "-b", "main")
    git_as(outer, "owner", "config", "user.name", "owner")
    target = outer / "inner"
    target.mkdir()
    (target / "tracked.txt").write_text("hi")
    git(outer, "add", "inner/tracked.txt")
    git_as(outer, "owner", "commit", "-q", "-m", "add tracked file")
    import shutil

    shutil.rmtree(target)
    result = run_kechup(
        ["new", str(target), "--goal", "goal text"], cwd=tmp_dir, agent="owner"
    )
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "outer_repo"


def test_new_nothing_written_on_exists_failure(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "unrelated.txt").write_text("hi")
    result = run_kechup(
        ["new", str(target), "--goal", "goal text"], cwd=tmp_dir, agent="owner"
    )
    assert result.exit_code == 1
    assert not (target / ".git").exists()
    assert not (target / ".kechup").exists()


def test_adopt_nothing_written_on_unsafe_file(tmp_dir: Path) -> None:
    # `adopt` always targets an existing folder (keche.md must already be
    # there), so its unsafe-file scan can fire on real pre-existing content.
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    secret = target / "id_rsa"
    secret.write_text("private key material")
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "unsafe_file"
    assert not (target / ".git").exists()
    assert not (target / ".kechup").exists()


def test_new_init_commit_carries_kechup_init(new_project: Path) -> None:
    result = git(new_project, "log", "--grep=^Kechup-Init: 1$", "--format=%H")
    assert result.stdout.strip()


def test_new_hook_installed(new_project: Path) -> None:
    hook = new_project / ".git" / "hooks" / "prepare-commit-msg"
    assert hook.exists()
    assert hook.stat().st_mode & 0o111


def test_new_crash_resumes_into_recovery(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    # Simulate a crash after step 6 (files written) but before step 8 (commit).
    (target / ".gitignore").write_text("*.tmp\n")
    kechup_dir = target / ".kechup"
    kechup_dir.mkdir()
    (kechup_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "next_id": 1, "tasks": {}})
    )
    (kechup_dir / "config.json").write_text(
        json.dumps({"version": 1, "owner_id": "owner", "created_at": "x", "created_by": "owner"})
    )
    (target / "keche.md").write_text("# proj\n")

    result = run_kechup(
        ["new", str(target), "--goal", "goal text"], cwd=tmp_dir, agent="owner"
    )
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["recovered"] is True
    assert (target / ".git").exists()
    init_log = git(target, "log", "--grep=^Kechup-Init: 1$", "--format=%H")
    assert init_log.stdout.strip()


def test_new_dry_run_not_applicable() -> None:
    # `new` has no --dry-run flag in spec section 5 (only `adopt` does).
    # Exercised instead by test_adopt_dry_run_* below.
    pass


def test_adopt_dry_run_writes_nothing_and_matches_real_run(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    dry = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert dry.exit_code == 0, dry.stderr
    assert dry.json["data"]["dry_run"] is True
    assert not (target / ".git").exists()
    assert not (target / ".kechup").exists()

    real = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
    assert real.exit_code == 0, real.stderr
    assert (target / ".git").exists()
    assert (target / ".kechup" / "tasks.json").exists()


def test_adopt_readopt_is_noop(new_project: Path, tmp_dir: Path) -> None:
    result = run_kechup(["adopt", str(new_project)], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["already_adopted"] is True


def test_adopt_no_keche_md_refused(tmp_dir: Path) -> None:
    target = tmp_dir / "foreign"
    target.mkdir()
    (target / "readme.txt").write_text("hi")
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "no_keche_md"


def test_adopt_nested_repo_refusal(tmp_dir: Path) -> None:
    outer = tmp_dir / "outer"
    outer.mkdir()
    git(outer, "init", "-q", "-b", "main")
    target = outer / "inner"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    git(outer, "add", "inner/keche.md")
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "outer_repo"


def test_repair_valid_files_commit(new_project: Path, tmp_dir: Path) -> None:
    add_result = run_kechup(
        ["add", "task", "Do a thing"], cwd=new_project, agent="owner"
    )
    assert add_result.exit_code == 0, add_result.stderr
    # Simulate a crash left mid-write: tasks.json edited on disk (new
    # title) but never committed, so keche.md's Work section is stale.
    tasks_json = new_project / ".kechup" / "tasks.json"
    tasks_json.write_text(tasks_json.read_text().replace("Do a thing", "Do a new thing"))
    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 0, result.stderr
    keche_md = new_project / "keche.md"
    assert "Do a new thing" in keche_md.read_text()
    assert git(new_project, "status", "--porcelain").stdout.strip() == ""


def test_repair_invalid_file_refuses(new_project: Path) -> None:
    (new_project / ".kechup" / "tasks.json").write_text("not json{{{")
    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 3


def test_repair_stale_hook_rewritten(new_project: Path) -> None:
    from kechup import gitutil

    hook_path = new_project / ".git" / "hooks" / "prepare-commit-msg"
    hook_path.write_text(f"#!/bin/sh\n{gitutil.HOOK_MARKER}\necho stale\n")
    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["hook_rewritten"] is True

    assert gitutil.hook_present_and_current(new_project)


def test_repair_refuses_foreign_hook(new_project: Path) -> None:
    hook_path = new_project / ".git" / "hooks" / "prepare-commit-msg"
    hook_path.write_text("#!/bin/sh\necho mine\n")
    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 2, result.stdout
    assert result.json["error"]["code"] == "foreign_hook"
    assert hook_path.read_text() == "#!/bin/sh\necho mine\n"


def test_adopt_recovery_commit_failure_refuses_not_success(tmp_dir: Path) -> None:
    """R6: when the recovery commit does not happen (a pre-commit hook
    exits 1), adopt must refuse, exit 3, never report recovered success."""
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / ".kechup").mkdir()
    git(target, "init", "-q", "--initial-branch=main")
    hooks_dir = target / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho blocked by test hook >&2\nexit 1\n")
    hook_path.chmod(0o755)

    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 3, result.stdout
    init_log = git(target, "log", "--grep=^Kechup-Init: 1$", "--format=%H", check=False)
    assert init_log.stdout.strip() == "", "no Kechup-Init commit must exist after a failed recovery"


def test_adopt_takes_project_lock_once_kechup_dir_exists(tmp_dir: Path) -> None:
    """R8: once `.kechup/` already exists on disk, `adopt` must hold the
    project lock through its write and commit, not only the bootstrap
    lock. We hold `.kechup/lock` ourselves and expect adopt to refuse."""
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "notes.txt").write_text("stray file: not a recovery candidate\n")
    (target / ".kechup").mkdir()

    lock_path = target / ".kechup" / "lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner")
        assert result.exit_code == 3, result.stdout
        assert result.json["error"]["code"] == "lock_timeout"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_repair_runs_revert_log_prelude(new_project: Path) -> None:
    """R5: `repair` must run the revert-log prelude itself, before its own
    body, not rely on some other write command to log a pending revert."""
    add_result = run_kechup(["add", "task", "Do a thing"], cwd=new_project, agent="owner")
    t1 = add_result.json["data"]["id"]
    show = run_kechup(["show", t1], cwd=new_project, agent="owner")
    folder = show.json["data"]["folder"]
    (new_project / folder / "REPORT.md").write_text("done work\n")
    run_kechup(
        ["commit", f"{folder}/REPORT.md", "-m", "add report", "--task", t1],
        cwd=new_project,
        agent="owner",
    )
    done = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert done.exit_code == 0, done.stderr
    done_commit = done.json["data"]["done_commit"]
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)

    before = git(new_project, "log", "--grep=^Kechup-Revert-Seen:", "--format=%H").stdout.strip()
    assert before == "", "revert must not be logged yet"

    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 0, result.stderr

    after = git(new_project, "log", "--grep=^Kechup-Revert-Seen:", "--format=%H").stdout.strip()
    assert after != "", "repair must run the revert-log prelude"


def test_repair_never_blocked_by_own_guard(new_project: Path) -> None:
    """repair is exempt from the dirty-metadata guard (section 6)."""
    (new_project / ".kechup" / "tasks.json").write_text(
        (new_project / ".kechup" / "tasks.json").read_text() + "\n"
    )
    result = run_kechup(["repair"], cwd=new_project, agent="owner")
    assert result.exit_code == 0, result.stderr
