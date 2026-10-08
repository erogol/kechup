"""Git rule tests (spec section 10, "Git tests")."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from conftest import git, run_kechup


def _add_task(project: Path, title: str) -> str:
    result = run_kechup(["add", "task", title], cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def test_author_and_message_format(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    author = git(new_project, "show", "-s", "--format=%an <%ae>", "HEAD").stdout.strip()
    assert author == "owner <owner@kechup.local>"
    body = git(new_project, "show", "-s", "--format=%B", "HEAD").stdout
    lines = body.splitlines()
    assert lines[0] == f"Add {t1}"
    assert lines[1] == ""
    assert lines[2] == f"Task: {t1}"


def test_unrelated_staged_file_not_swept_in(new_project: Path) -> None:
    (new_project / "a.txt").write_text("a")
    (new_project / "b.txt").write_text("b")
    git(new_project, "add", "b.txt")
    result = run_kechup(["commit", "a.txt", "-m", "add a only"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    commit_files = git(new_project, "show", "--stat", "--format=", "-1").stdout
    assert "a.txt" in commit_files
    assert "b.txt" not in commit_files
    status = git(new_project, "status", "--porcelain", "--", "b.txt").stdout
    assert status.strip().startswith("A")


def test_log_grep_finds_commits(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    result = run_kechup(["log", t1], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    subjects = [e["subject"] for e in result.json["data"]["entries"]]
    assert subjects == [f"Add {t1}", f"Claim {t1}"]


def test_foreign_repo_refusal_does_not_fire_for_new_adopt(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    result = run_kechup(
        ["new", str(target), "--goal", "goal text"], cwd=tmp_dir, agent="owner"
    )
    assert result.exit_code == 0, result.stderr


def test_tag_creation_and_duplicate_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    first = run_kechup(["approve", t1, "spec"], cwd=new_project, agent="x")
    assert first.exit_code == 0, first.stderr
    assert git(new_project, "tag", "-l", f"{t1}-spec-approved").stdout.strip()
    second = run_kechup(["approve", t1, "spec"], cwd=new_project, agent="x")
    assert second.exit_code == 2
    assert second.json["error"]["code"] == "tag_exists"


def test_index_lock_retry(new_project: Path) -> None:
    lock_path = new_project / ".git" / "index.lock"
    lock_path.write_text("")

    import threading

    def _release_after_delay() -> None:
        time.sleep(0.3)
        lock_path.unlink()

    thread = threading.Thread(target=_release_after_delay)
    thread.start()
    result = run_kechup(["add", "task", "x"], cwd=new_project, agent="owner")
    thread.join()
    assert result.exit_code == 0, result.stderr


def test_git_literal_pathspecs_on_path_taking_call(new_project: Path) -> None:
    weird_name = ":(glob)**"
    (new_project / weird_name).write_text("literal content")
    result = run_kechup(["commit", weird_name, "-m", "literal pathspec"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    assert weird_name in result.json["data"]["paths"]
    status = git(new_project, "status", "--porcelain").stdout
    assert status.strip() == ""


def test_hook_fires_on_humans_own_commit_too(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    done_show_folder = run_kechup(["show", t1], cwd=new_project, agent="a").json["data"]["folder"]
    (new_project / done_show_folder / "REPORT.md").write_text("done\n")
    run_kechup(
        ["commit", f"{done_show_folder}/REPORT.md", "-m", "report", "--task", t1],
        cwd=new_project, agent="a",
    )
    done = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    done_commit = done.json["data"]["done_commit"]

    result = subprocess.run(
        ["git", "revert", "--no-commit", done_commit],
        cwd=str(new_project), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    import os

    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "a-human"
    env["GIT_AUTHOR_EMAIL"] = "a-human@test.local"
    env["GIT_COMMITTER_NAME"] = "a-human"
    env["GIT_COMMITTER_EMAIL"] = "a-human@test.local"
    commit_result = subprocess.run(
        ["git", "commit", "-m", "a human's own revert commit"],
        cwd=str(new_project), capture_output=True, text=True, env=env,
    )
    assert commit_result.returncode == 0, commit_result.stderr
    body = git(new_project, "show", "-s", "--format=%B", "HEAD").stdout
    assert "Kechup-Reverts:" in body
    assert done_commit in body
