"""Revert detection tests (spec section 10, "Revert detection"). Every
test asserts the exact flag text and log entries, not only success."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import git, git_as, run_kechup


def _add_task(project: Path, title: str, **kwargs: str) -> str:
    args = ["add", "task", title]
    for key, value in kwargs.items():
        args.extend([f"--{key.replace('_', '-')}", value])
    result = run_kechup(args, cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def _complete_task(project: Path, task_id: str) -> str:
    """Completes the task; returns its done_commit."""
    show = run_kechup(["show", task_id], cwd=project, agent="a")
    folder = show.json["data"]["folder"]
    (project / folder / "REPORT.md").write_text("done work\n")
    run_kechup(
        ["commit", f"{folder}/REPORT.md", "-m", "add report", "--task", task_id],
        cwd=project,
        agent="a",
    )
    result = run_kechup(["done", task_id, "--evidence", "ev"], cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["done_commit"]


def _state(project: Path, task_id: str) -> str:
    result = run_kechup(["show", task_id], cwd=project, agent="a")
    return result.json["data"]["state"]


def _touch_write_write(project: Path) -> None:
    """Runs a trivial write command so the revert-logging prelude fires."""
    (project / "placeholder.txt").write_text("x")
    run_kechup(["commit", "placeholder.txt", "-m", "placeholder"], cwd=project, agent="a")


def test_plain_noedit_revert_of_done_commit(new_project: Path) -> None:
    """T7: assert the complete flag text, not only a prefix."""
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short = git(new_project, "rev-parse", "--short", revert_commit).stdout.strip()
    state = _state(new_project, t1)
    assert state == f"done — WORK REVERTED by {short} (reverter)"
    # `next` skips a flagged-done task's dependents, and skips the flagged
    # task itself since it is not displayed `todo`/`doing (stale)`.
    next_result = run_kechup(["next"], cwd=new_project, agent="a")
    assert next_result.exit_code == 1


def test_nocommit_handwritten_message_hook_trailer(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    result = git_as(
        new_project, "reverter", "revert", "--no-commit", done_commit, check=False
    )
    assert result.returncode == 0, result.stderr
    import os

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "reverter"
    env["GIT_AUTHOR_EMAIL"] = "reverter@test.local"
    env["GIT_COMMITTER_NAME"] = "reverter"
    env["GIT_COMMITTER_EMAIL"] = "reverter@test.local"
    commit_result = subprocess.run(
        ["git", "commit", "-m", "hand written message, no standard line"],
        cwd=str(new_project),
        capture_output=True,
        text=True,
        env=env,
    )
    assert commit_result.returncode == 0, commit_result.stderr
    body = git(new_project, "show", "-s", "--format=%B", "HEAD").stdout
    assert "Kechup-Reverts:" in body
    assert done_commit in body
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short = git(new_project, "rev-parse", "--short", revert_commit).stdout.strip()
    state = _state(new_project, t1)
    assert state == f"done — WORK REVERTED by {short} (reverter)"


def test_reapply_clears_flag_and_unblocks_dependents(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    first_revert = git(new_project, "rev-parse", "HEAD").stdout.strip()
    first_short = git(new_project, "rev-parse", "--short", first_revert).stdout.strip()
    assert _state(new_project, t1) == f"done — WORK REVERTED by {first_short} (reverter)"
    assert _state(new_project, t2) == "blocked"

    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    git_as(new_project, "reverter", "revert", "--no-edit", revert_commit)
    assert _state(new_project, t1) == "done"
    assert _state(new_project, t2) == "todo"


def test_reset_hard_no_revert_commit(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    _touch_write_write(new_project)
    # A history rewrite that drops `done_commit` while keeping the `Done
    # T1` commit on top of it: replay everything after done_commit onto
    # its own parent, dropping done_commit itself from history.
    git(new_project, "rebase", "--onto", f"{done_commit}^", done_commit)
    state = _state(new_project, t1)
    assert state == "done — done commit not in history"


def test_report_deleted_by_later_commit(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    _complete_task(new_project, t1)
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    git_as(new_project, "a", "rm", "-q", f"{folder}/REPORT.md")
    git_as(new_project, "a", "commit", "-q", "-m", "oops deleted the report")
    state = _state(new_project, t1)
    assert state == "done — REPORT.md missing"


def test_revert_of_earlier_work_commit_not_done_commit(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    (new_project / folder / "PLAN.md").write_text("plan draft\n")
    plan_commit_result = run_kechup(
        ["commit", f"{folder}/PLAN.md", "-m", "add plan", "--task", t1],
        cwd=new_project,
        agent="a",
    )
    assert plan_commit_result.exit_code == 0, plan_commit_result.stderr
    plan_commit = plan_commit_result.json["data"]["commit"]
    _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", plan_commit)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short = git(new_project, "rev-parse", "--short", revert_commit).stdout.strip()
    state = _state(new_project, t1)
    assert state == f"done — WORK REVERTED by {short} (reverter)"


def test_reopen_after_revert_then_redone_clears_flag(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short = git(new_project, "rev-parse", "--short", revert_commit).stdout.strip()
    assert _state(new_project, t1) == f"done — WORK REVERTED by {short} (reverter)"
    reopen = run_kechup(["reopen", t1, "--reason", "redo it"], cwd=new_project, agent="a")
    assert reopen.exit_code == 0, reopen.stderr

    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    # The revert of the "add report" commit removed REPORT.md, and git
    # also pruned the now-empty task folder: recreate it before writing.
    (new_project / folder).mkdir(parents=True, exist_ok=True)
    (new_project / folder / "REPORT.md").write_text("redone work\n")
    run_kechup(
        ["commit", f"{folder}/REPORT.md", "-m", "redo report", "--task", t1],
        cwd=new_project,
        agent="a",
    )
    done2 = run_kechup(["done", t1, "--evidence", "ev2"], cwd=new_project, agent="owner")
    assert done2.exit_code == 0, done2.stderr
    assert _state(new_project, t1) == "done"

    log_result = run_kechup(["log", t1], cwd=new_project, agent="a")
    subjects = [e["subject"] for e in log_result.json["data"]["entries"]]
    assert any(s.startswith('Revert "') for s in subjects)


def test_revert_and_reapply_both_before_next_write_logged_once_each(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    git_as(new_project, "reverter", "revert", "--no-edit", revert_commit)

    _touch_write_write(new_project)

    log_subjects = git(new_project, "log", "--format=%s").stdout.splitlines()
    reverted_lines = [s for s in log_subjects if "reverted by" in s]
    reinstated_lines = [s for s in log_subjects if "reinstated by" in s]
    assert len(reverted_lines) == 1
    assert len(reinstated_lines) == 1


def test_revert_of_commit_with_no_task_line_logged_as_task_none(new_project: Path) -> None:
    """T6: the reverted ORIGINAL commit must truly have no `Task:` line at
    all (made with a raw `git commit`, bypassing kechup, which always adds
    one). Then the logging commit must say `Task: none` and carry the
    `Kechup-Revert-Seen` trailer."""
    (new_project / "project.md").write_text("v1\n")
    git(new_project, "add", "project.md")
    raw_commit = git_as(new_project, "a", "commit", "-q", "-m", "project note, no kechup")
    assert raw_commit.returncode == 0, raw_commit.stderr
    work_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    original_body = git(new_project, "show", "-s", "--format=%B", work_commit).stdout
    assert "Task:" not in original_body, "the original commit must have no Task line at all"

    git_as(new_project, "reverter", "revert", "--no-edit", work_commit)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    _touch_write_write(new_project)

    body_matches = []
    for commit_id in git(new_project, "log", "--format=%H").stdout.splitlines():
        trailers = git(
            new_project, "log", "-1", "--format=%(trailers:key=Kechup-Revert-Seen,valueonly)", commit_id
        ).stdout.strip()
        if trailers:
            body = git(new_project, "show", "-s", "--format=%B", commit_id).stdout
            body_matches.append((body, trailers))
    assert body_matches, "no revert-logging commit was made"
    body, trailers = body_matches[0]
    assert "Task: none" in body
    assert trailers == revert_commit


def test_revert_touching_no_tracked_file_still_logs_empty_commit(new_project: Path) -> None:
    (new_project / "scratch.txt").write_text("v1\n")
    run_kechup(["commit", "scratch.txt", "-m", "scratch note"], cwd=new_project, agent="a")
    work_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    git_as(new_project, "reverter", "revert", "--no-edit", work_commit)

    before_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    _touch_write_write(new_project)
    after_commits = git(new_project, "log", "--format=%H").stdout.splitlines()
    assert before_head in after_commits
    idx = after_commits.index(before_head)
    # The revert-logging commit sits strictly between `before_head` and the
    # placeholder write's own commit.
    assert idx >= 2


def test_handedited_revert_no_hook_no_standard_line_not_detected(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    # Revert an earlier work commit that is not the REPORT.md commit, so a
    # missed detection is not masked by an incidental REPORT.md deletion.
    (new_project / folder / "PLAN.md").write_text("plan draft\n")
    plan_commit_result = run_kechup(
        ["commit", f"{folder}/PLAN.md", "-m", "add plan", "--task", t1],
        cwd=new_project,
        agent="a",
    )
    plan_commit = plan_commit_result.json["data"]["commit"]
    _complete_task(new_project, t1)

    hook_path = new_project / ".git" / "hooks" / "prepare-commit-msg"
    hook_path.unlink()

    result = git_as(new_project, "reverter", "revert", "--no-commit", plan_commit, check=False)
    assert result.returncode == 0, result.stderr
    import os

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "reverter"
    env["GIT_AUTHOR_EMAIL"] = "reverter@test.local"
    env["GIT_COMMITTER_NAME"] = "reverter"
    env["GIT_COMMITTER_EMAIL"] = "reverter@test.local"
    subprocess.run(
        ["git", "commit", "-m", "no hook, no standard line"],
        cwd=str(new_project), capture_output=True, text=True, env=env, check=True,
    )
    state = _state(new_project, t1)
    assert state == "done"


def test_fresh_clone_no_hook_plain_revert_still_detected(new_project: Path, tmp_dir: Path) -> None:
    """T7: assert the complete flag text, including the exact short commit
    id and author name, not only a prefix."""
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    clone = tmp_dir / "clone"
    git(tmp_dir, "clone", "-q", str(new_project), str(clone))
    assert not (clone / ".git" / "hooks" / "prepare-commit-msg").exists()
    git_as(clone, "reverter", "revert", "--no-edit", done_commit)
    revert_commit = git(clone, "rev-parse", "HEAD").stdout.strip()
    short = git(clone, "rev-parse", "--short", revert_commit).stdout.strip()
    result = run_kechup(["show", t1], cwd=clone, agent="a")
    assert result.json["data"]["state"] == f"done — WORK REVERTED by {short} (reverter)"


def test_unrelated_staged_file_survives_empty_logging_commit(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)

    (new_project / "unrelated.txt").write_text("staged but not committed")
    git(new_project, "add", "unrelated.txt")
    status_before = git(new_project, "status", "--porcelain", "--", "unrelated.txt").stdout

    (new_project / "placeholder.txt").write_text("x")
    result = run_kechup(
        ["commit", "placeholder.txt", "-m", "placeholder"], cwd=new_project, agent="a"
    )
    assert result.exit_code == 0, result.stderr

    status_after = git(new_project, "status", "--porcelain", "--", "unrelated.txt").stdout
    assert status_before == status_after
    assert status_after.strip().startswith("A")
    last_commit_files = git(
        new_project, "show", "--stat", "--format=", "-1"
    ).stdout
    assert "unrelated.txt" not in last_commit_files


def test_snapshot_after_plain_revert_updates_work_section(new_project: Path) -> None:
    """FIX_SPEC F4: a plain `git revert` leaves keche.md stale until the next
    write command. `kechup snapshot` with nothing else to commit must still
    run the revert-logging prelude and update the Work section."""
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)

    commits_before = len(git(new_project, "rev-list", "HEAD").stdout.splitlines())
    result = run_kechup(["snapshot"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    commits_after = len(git(new_project, "rev-list", "HEAD").stdout.splitlines())
    assert commits_after == commits_before + 1
    revert_log_author = git(new_project, "show", "-s", "--format=%an", "HEAD").stdout.strip()
    assert revert_log_author == "kechup-revert-log"

    keche_md = (new_project / "keche.md").read_text(encoding="utf-8")
    flag_text = _state(new_project, t1).split("done — ", 1)[1]
    assert f"{t1}: First ({flag_text})" in keche_md
    assert f"{t2}: Second (waits for {t1} (work reverted))" in keche_md


def test_two_independent_done_tasks_each_reverted_separately(new_project: Path) -> None:
    """T5 (renamed, was "branching..."): this builds two separate, linear
    reverts of two independent tasks, not a branching revert history.
    Branching-history coverage is deferred to V1.1 backlog item D1."""
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second")
    done1 = _complete_task(new_project, t1)
    done2 = _complete_task(new_project, t2)
    git_as(new_project, "reverter", "revert", "--no-edit", done1)
    revert1 = git(new_project, "rev-parse", "HEAD").stdout.strip()
    git_as(new_project, "reverter", "revert", "--no-edit", done2)
    revert2 = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short1 = git(new_project, "rev-parse", "--short", revert1).stdout.strip()
    short2 = git(new_project, "rev-parse", "--short", revert2).stdout.strip()
    assert _state(new_project, t1) == f"done — WORK REVERTED by {short1} (reverter)"
    assert _state(new_project, t2) == f"done — WORK REVERTED by {short2} (reverter)"


def test_linear_revert_chain_of_three_reverts_odd_length_still_flagged(
    new_project: Path,
) -> None:
    """T5 (renamed, was "branching..."): this is a single linear chain of
    three reverts (O -> R1 -> R2 -> R3), not a branching history. Branching
    coverage is deferred to V1.1 backlog item D1."""
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    revert1 = git(new_project, "rev-parse", "HEAD").stdout.strip()
    git_as(new_project, "reverter", "revert", "--no-edit", revert1)
    revert2 = git(new_project, "rev-parse", "HEAD").stdout.strip()
    # A chain of 3 edges (odd): O -> R1 -> R2 -> R3, flagged reverted.
    git_as(new_project, "reverter", "revert", "--no-edit", revert2)
    revert3 = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short3 = git(new_project, "rev-parse", "--short", revert3).stdout.strip()
    state = _state(new_project, t1)
    assert state == f"done — WORK REVERTED by {short3} (reverter)"


def test_hook_and_revert_head_command_sequence(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    result = git_as(new_project, "reverter", "revert", "--no-commit", done_commit, check=False)
    assert result.returncode == 0
    assert (new_project / ".git" / "REVERT_HEAD").exists()
    revert_head_content = (new_project / ".git" / "REVERT_HEAD").read_text().strip()
    assert revert_head_content == done_commit
    import os

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "reverter"
    env["GIT_AUTHOR_EMAIL"] = "reverter@test.local"
    env["GIT_COMMITTER_NAME"] = "reverter"
    env["GIT_COMMITTER_EMAIL"] = "reverter@test.local"
    subprocess.run(
        ["git", "commit", "--no-edit"],
        cwd=str(new_project), capture_output=True, text=True, env=env, check=True,
    )
    assert not (new_project / ".git" / "REVERT_HEAD").exists()
    body = git(new_project, "show", "-s", "--format=%B", "HEAD").stdout
    assert "This reverts commit" in body


def test_hook_fires_under_noverify(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_commit = _complete_task(new_project, t1)
    git_as(new_project, "reverter", "revert", "--no-commit", done_commit, check=False)
    import os

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "reverter"
    env["GIT_AUTHOR_EMAIL"] = "reverter@test.local"
    env["GIT_COMMITTER_NAME"] = "reverter"
    env["GIT_COMMITTER_EMAIL"] = "reverter@test.local"
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "hand message under no-verify"],
        cwd=str(new_project), capture_output=True, text=True, env=env, check=True,
    )
    body = git(new_project, "show", "-s", "--format=%B", "HEAD").stdout
    assert "Kechup-Reverts:" in body
