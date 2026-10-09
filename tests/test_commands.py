"""Per-command tests (spec section 10, "Per command") for add/claim/note/
block/assign/done/reopen/drop/dep/list/next/show/log/commit/approve/
snapshot/config."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git, git_as, run_kechup


def _add_task(project: Path, title: str, **kwargs: str) -> str:
    args = ["add", "task", title]
    for key, value in kwargs.items():
        args.extend([f"--{key.replace('_', '-')}", value])
    result = run_kechup(args, cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def test_add_task_id_sequence(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second")
    assert t1 == "T1"
    assert t2 == "T2"


def test_add_task_bad_after_id(new_project: Path) -> None:
    result = run_kechup(
        ["add", "task", "First", "--after", "T99"], cwd=new_project, agent="owner"
    )
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "unknown_task"


def test_add_task_cycle_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    result = run_kechup(
        ["add", "task", "Third", "--after", f"{t1},{t2}"], cwd=new_project, agent="owner"
    )
    assert result.exit_code == 0
    t3 = result.json["data"]["id"]
    cycle_result = run_kechup(
        ["dep", "add", t1, "--after", t3], cwd=new_project, agent="owner"
    )
    assert cycle_result.exit_code == 1
    assert cycle_result.json["error"]["code"] == "cycle"


def test_add_task_folder_escape_refusal(new_project: Path) -> None:
    result = run_kechup(
        ["add", "task", "Escapee", "--folder", "../outside"],
        cwd=new_project,
        agent="owner",
    )
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "bad_folder"


def test_add_task_folder_reuse(new_project: Path) -> None:
    existing = new_project / "docs"
    existing.mkdir()
    result = run_kechup(
        ["add", "task", "Use docs", "--folder", "docs"], cwd=new_project, agent="owner"
    )
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["folder"] == "docs"


def test_claim_unclaim_note_happy_path(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    claim = run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    assert claim.exit_code == 0, claim.stderr
    note = run_kechup(["note", t1, "making progress"], cwd=new_project, agent="agent-a")
    assert note.exit_code == 0, note.stderr
    unclaim = run_kechup(["unclaim", t1], cwd=new_project, agent="agent-a")
    assert unclaim.exit_code == 0, unclaim.stderr
    show = run_kechup(["show", t1], cwd=new_project, agent="agent-a")
    assert show.json["data"]["state"] == "todo"


def test_claim_wrong_state_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    result = run_kechup(["claim", t1], cwd=new_project, agent="agent-b")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "already_claimed"


def test_agent_id_blank_after_strip_refuses(new_project: Path) -> None:
    """R4: a blank `--by` (spaces only) must refuse, not pass through. A
    blank `KECHUP_AGENT` must count as missing. A valid `--by` wins over a
    valid env value."""
    t1 = _add_task(new_project, "Do work")

    by_blank = run_kechup(["claim", t1, "--by", "   "], cwd=new_project)
    assert by_blank.exit_code == 3, by_blank.stdout
    assert by_blank.json["error"]["code"] == "no_agent_id"

    env_blank = run_kechup(["claim", t1], cwd=new_project, extra_env={"KECHUP_AGENT": "  "})
    assert env_blank.exit_code == 3, env_blank.stdout
    assert env_blank.json["error"]["code"] == "no_agent_id"

    by_wins = run_kechup(
        ["claim", t1, "--by", "flag-agent"],
        cwd=new_project,
        extra_env={"KECHUP_AGENT": "env-agent"},
    )
    assert by_wins.exit_code == 0, by_wins.stdout
    author = git(new_project, "show", "-s", "--format=%an", "HEAD").stdout.strip()
    assert author == "flag-agent"


def test_note_by_non_owner_records_author(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    tasks_json = new_project / ".kechup" / "tasks.json"
    claimed_before = json.loads(tasks_json.read_text())["tasks"][t1]["claimed_at"]
    result = run_kechup(["note", t1, "hi from helper"], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr
    task = json.loads(tasks_json.read_text())["tasks"][t1]
    assert task["notes"][-1]["by"] == "agent-b"
    assert task["notes"][-1]["text"] == "hi from helper"
    assert task["owner"] == "agent-a"
    assert task["claimed_at"] == claimed_before
    assert task["last_note_at"] is not None
    author = git(new_project, "show", "-s", "--format=%an", "HEAD").stdout.strip()
    assert author == "agent-b"


def test_note_show_prints_author(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    run_kechup(["note", t1, "helper note"], cwd=new_project, agent="agent-b")
    show = run_kechup(["show", t1], cwd=new_project, agent="agent-a", as_json=False)
    assert show.exit_code == 0, show.stderr
    assert "by=agent-b" in show.stdout
    assert "text=helper note" in show.stdout


def test_note_not_doing_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    result = run_kechup(["note", t1, "hi"], cwd=new_project, agent="agent-a")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "not_doing"


def test_note_without_agent_id_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    result = run_kechup(["note", t1, "hi"], cwd=new_project, agent=None)
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "no_agent_id"
    task = json.loads((new_project / ".kechup" / "tasks.json").read_text())["tasks"][t1]
    assert task["notes"] == []


def test_unclaim_wrong_owner_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    result = run_kechup(["unclaim", t1], cwd=new_project, agent="agent-b")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "not_owner"


def test_claim_stale_takeover(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    tasks_json = new_project / ".kechup" / "tasks.json"
    data = json.loads(tasks_json.read_text())
    data["tasks"][t1]["claimed_at"] = "2000-01-01T00:00:00+00:00"
    tasks_json.write_text(json.dumps(data))
    git(new_project, "add", ".kechup/tasks.json")
    git_as(new_project, "owner", "commit", "-q", "-m", "backdate for test")
    result = run_kechup(["claim", t1], cwd=new_project, agent="agent-b")
    assert result.exit_code == 0, result.stderr


def test_block_unblock(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    block = run_kechup(["block", t1, "--reason", "waiting on input"], cwd=new_project, agent="a")
    assert block.exit_code == 0, block.stderr
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert show.json["data"]["state"] == "blocked"
    unblock = run_kechup(["unblock", t1], cwd=new_project, agent="a")
    assert unblock.exit_code == 0, unblock.stderr
    show2 = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert show2.json["data"]["state"] == "todo"


def test_assign_any_caller(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    result = run_kechup(["assign", t1, "--to", "agent-x"], cwd=new_project, agent="agent-y")
    assert result.exit_code == 0, result.stderr
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert show.json["data"]["owner"] == "agent-x"


def test_assign_done_task_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    result = run_kechup(["assign", t1, "--to", "agent-x"], cwd=new_project, agent="agent-y")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "terminal_state"


def _complete_task(project: Path, task_id: str, title_slug: str | None = None) -> None:
    show = run_kechup(["show", task_id], cwd=project, agent="a")
    folder = show.json["data"]["folder"]
    (project / folder / "REPORT.md").write_text("done work\n")
    commit = run_kechup(
        ["commit", f"{folder}/REPORT.md", "-m", "add report", "--task", task_id],
        cwd=project,
        agent="a",
    )
    assert commit.exit_code == 0, commit.stderr
    done = run_kechup(["done", task_id, "--evidence", "ev"], cwd=project, agent="owner")
    assert done.exit_code == 0, done.stderr


def test_done_non_owner_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    (new_project / folder / "REPORT.md").write_text("done work\n")
    run_kechup(["commit", f"{folder}/REPORT.md", "-m", "r", "--task", t1], cwd=new_project, agent="a")
    result = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="not-owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "not_owner"


def test_done_missing_report_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    result = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "no_report"


def test_done_dirty_tree_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    report = new_project / folder / "REPORT.md"
    report.write_text("done work\n")
    run_kechup(["commit", f"{folder}/REPORT.md", "-m", "r", "--task", t1], cwd=new_project, agent="a")
    report.write_text("different content now\n")
    result = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "dirty_report"


def test_done_owner_success_and_done_commit_recorded(new_project: Path) -> None:
    """T2: `done_commit` must equal the HEAD commit id captured right
    before `done` runs, not just any 40-hex string."""
    t1 = _add_task(new_project, "Do work")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    (new_project / folder / "REPORT.md").write_text("done work\n")
    commit = run_kechup(
        ["commit", f"{folder}/REPORT.md", "-m", "add report", "--task", t1],
        cwd=new_project,
        agent="a",
    )
    assert commit.exit_code == 0, commit.stderr
    before_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    done = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert done.exit_code == 0, done.stderr

    show2 = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert show2.json["data"]["state"] == "done"
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][t1]["done_commit"]
    assert done_commit == before_head


def test_reopen_clears_fields_and_keeps_note(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    result = run_kechup(["reopen", t1, "--reason", "needs more work"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    task = tasks_json["tasks"][t1]
    assert task["done_commit"] is None
    assert task["evidence"] is None
    assert task["done_at"] is None
    assert task["state"] == "todo"
    assert any(n["text"] == "needs more work" for n in task["notes"])


def test_reopen_refused_on_non_done_task(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    result = run_kechup(["reopen", t1, "--reason", "x"], cwd=new_project, agent="a")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "not_done"


def test_reopen_works_on_flagged_task(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][t1]["done_commit"]
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    show_before = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert "WORK REVERTED" in show_before.json["data"]["state"]
    result = run_kechup(["reopen", t1, "--reason", "fix after revert"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    show_after = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert show_after.json["data"]["state"] == "todo"


def test_drop_downstream_task_stays_blocked(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    drop = run_kechup(["drop", t1, "--reason", "no longer needed"], cwd=new_project, agent="a")
    assert drop.exit_code == 0, drop.stderr
    show = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert show.json["data"]["state"] == "blocked"
    assert "dropped" in show.json["data"]["blocker_cause"]


def test_dep_add_unknown_id(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    result = run_kechup(["dep", "add", t1, "--after", "T99"], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "unknown_task"


def test_dep_add_cycle(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    result = run_kechup(["dep", "add", t1, "--after", t2], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "cycle"


def test_dep_rm(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    result = run_kechup(["dep", "rm", t2, t1], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    show = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert t1 not in show.json["data"]["after"]


def test_dep_rm_unknown_task_id(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    result = run_kechup(["dep", "rm", "T99", t1], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "unknown_task"


def test_dep_rm_unknown_after_id(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    result = run_kechup(["dep", "rm", t1, "T99"], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "unknown_task"


def test_dep_rm_not_in_after(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second")
    result = run_kechup(["dep", "rm", t2, t1], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "not_in_after"
    assert result.json["error"]["message"] == f"{t1} is not in after of {t2}"
    show = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert show.json["data"]["after"] == []


def test_list_json_shape_and_state_open(new_project: Path) -> None:
    """T3: with one fixture task in every state (todo, doing, blocked,
    done, dropped), `--state open` must be exactly todo+doing+blocked."""
    t_todo = _add_task(new_project, "Todo task")
    t_doing = _add_task(new_project, "Doing task")
    run_kechup(["claim", t_doing], cwd=new_project, agent="agent-x")
    t_blocked = _add_task(new_project, "Blocked task")
    run_kechup(["block", t_blocked, "--reason", "x"], cwd=new_project, agent="a")
    t_done = _add_task(new_project, "Done task")
    _complete_task(new_project, t_done)
    t_dropped = _add_task(new_project, "Dropped task")
    run_kechup(["drop", t_dropped, "--reason", "no longer needed"], cwd=new_project, agent="a")

    result = run_kechup(["list", "--state", "open"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    ids = {row["id"] for row in result.json["data"]["tasks"]}
    assert ids == {t_todo, t_doing, t_blocked}

    result_done = run_kechup(["list", "--state", "done"], cwd=new_project, agent="a")
    assert {row["id"] for row in result_done.json["data"]["tasks"]} == {t_done}

    result_dropped = run_kechup(["list", "--state", "dropped"], cwd=new_project, agent="a")
    assert {row["id"] for row in result_dropped.json["data"]["tasks"]} == {t_dropped}


def test_next_dependency_chain(new_project: Path) -> None:
    t1 = _add_task(new_project, "First", priority="1")
    t2 = _add_task(new_project, "Second", after=t1, priority="1")
    result = run_kechup(["next"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["id"] == t1


def test_next_claim(new_project: Path) -> None:
    _add_task(new_project, "First")
    result = run_kechup(["next", "--claim"], cwd=new_project, agent="agent-z")
    assert result.exit_code == 0, result.stderr
    show = run_kechup(["show", result.json["data"]["id"]], cwd=new_project, agent="a")
    assert show.json["data"]["owner"] == "agent-z"


def test_next_no_open_task(new_project: Path) -> None:
    result = run_kechup(["next"], cwd=new_project, agent="a")
    assert result.exit_code == 1
    assert result.json["error"]["code"] == "no_open_task"


def test_log_includes_joined_revert_commit(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][t1]["done_commit"]
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)
    result = run_kechup(["log", t1], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    subjects = [e["subject"] for e in result.json["data"]["entries"]]
    assert any(s.startswith('Revert "') for s in subjects)


def test_log_entry_dates_non_empty_iso8601(new_project: Path) -> None:
    """FIX_SPEC F2: every log entry, including the joined revert commit,
    has a non-empty ISO 8601 date."""
    from datetime import datetime

    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][t1]["done_commit"]
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)

    result = run_kechup(["log", t1], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    entries = result.json["data"]["entries"]
    assert len(entries) >= 2
    for entry in entries:
        assert entry["date"], entry
        datetime.fromisoformat(entry["date"])


def test_commit_explicit_path_only_leaves_others_staged(new_project: Path) -> None:
    (new_project / "docs").mkdir(exist_ok=True)
    (new_project / "docs" / "a.md").write_text("a")
    (new_project / "docs" / "b.md").write_text("b")
    git(new_project, "add", "docs/b.md")
    result = run_kechup(
        ["commit", "docs/a.md", "-m", "add a"], cwd=new_project, agent="x"
    )
    assert result.exit_code == 0, result.stderr
    status = git(new_project, "status", "--porcelain").stdout
    assert "A  docs/b.md" in status


def test_commit_directory_expansion(new_project: Path) -> None:
    (new_project / "docs").mkdir(exist_ok=True)
    (new_project / "docs" / "a.md").write_text("a")
    (new_project / "docs" / "b.md").write_text("b")
    result = run_kechup(["commit", "docs", "-m", "add docs"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    assert set(result.json["data"]["paths"]) == {"docs/a.md", "docs/b.md"}


def test_commit_size_refusal(new_project: Path) -> None:
    big = new_project / "big.bin"
    big.write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    result = run_kechup(["commit", "big.bin", "-m", "big"], cwd=new_project, agent="x")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "too_large"


def test_commit_secret_refusal(new_project: Path) -> None:
    secret = new_project / "id_rsa"
    secret.write_text("private")
    result = run_kechup(["commit", "id_rsa", "-m", "oops"], cwd=new_project, agent="x")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "secret_like"


def test_commit_path_escape_refusal(new_project: Path, tmp_dir: Path) -> None:
    sibling = tmp_dir / "proj-other"
    sibling.mkdir()
    (sibling / "f.txt").write_text("hi")
    result = run_kechup(
        ["commit", "../proj-other/f.txt", "-m", "escape"], cwd=new_project, agent="x"
    )
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "path_escape"


def test_commit_sibling_folder_not_contained(new_project: Path, tmp_dir: Path) -> None:
    sibling = tmp_dir / f"{new_project.name}-other"
    sibling.mkdir()
    (sibling / "f.txt").write_text("hi")
    rel = f"../{sibling.name}/f.txt"
    result = run_kechup(["commit", rel, "-m", "sibling"], cwd=new_project, agent="x")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "path_escape"

    # T1: the absolute path of the same sibling file, not a `..` path.
    before_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    absolute = run_kechup(
        ["commit", str(sibling / "f.txt"), "-m", "sibling abs"], cwd=new_project, agent="x"
    )
    assert absolute.exit_code == 3
    assert absolute.json["error"]["code"] == "path_escape"
    after_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    assert after_head == before_head
    assert git(new_project, "status", "--porcelain").stdout.strip() == ""


def test_commit_directory_with_symlink_outside_project_refuses(
    new_project: Path, tmp_dir: Path
) -> None:
    """R9: a directory expansion that would include a symlink pointing
    outside the project must refuse, exit 3, no write."""
    outside = tmp_dir / "outside.txt"
    outside.write_text("secret elsewhere")
    docs = new_project / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("a")
    (docs / "link.txt").symlink_to(outside)

    before_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    result = run_kechup(["commit", "docs", "-m", "add docs"], cwd=new_project, agent="x")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "path_escape"
    after_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    assert after_head == before_head


def test_commit_direct_path_into_nested_repo_refuses(new_project: Path) -> None:
    """R9: a direct path into a subfolder that has its own `.git` must
    refuse, exit 3."""
    nested = new_project / "vendor"
    nested.mkdir()
    git(nested, "init", "-q", "--initial-branch=main")
    (nested / "lib.py").write_text("x = 1\n")
    result = run_kechup(
        ["commit", "vendor/lib.py", "-m", "add vendored file"], cwd=new_project, agent="x"
    )
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "path_escape"


def test_commit_glob_style_filename_is_literal(new_project: Path) -> None:
    weird_name = ":(glob)**"
    weird_path = new_project / weird_name
    weird_path.write_text("literal file")
    result = run_kechup(["commit", weird_name, "-m", "literal"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    assert weird_name in result.json["data"]["paths"]


def test_commit_new_file_needs_add_first(new_project: Path) -> None:
    new_file = new_project / "new.txt"
    new_file.write_text("new content")
    result = run_kechup(["commit", "new.txt", "-m", "add new"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    status = git(new_project, "status", "--porcelain", "--", "new.txt").stdout
    assert status.strip() == ""


def test_approve_tag_exists_and_duplicate_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    result = run_kechup(["approve", t1, "spec"], cwd=new_project, agent="x")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["tag"] == f"{t1}-spec-approved"
    dup = run_kechup(["approve", t1, "spec"], cwd=new_project, agent="x")
    assert dup.exit_code == 2
    assert dup.json["error"]["code"] == "tag_exists"


def test_approve_uncommitted_changes_refusal(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    show = run_kechup(["show", t1], cwd=new_project, agent="a")
    folder = show.json["data"]["folder"]
    (new_project / folder / "SPEC.md").write_text("spec draft")
    result = run_kechup(["approve", t1, "spec"], cwd=new_project, agent="x")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "dirty_tree"


def test_show_rebuild_md_requires_agent_and_commits(new_project: Path) -> None:
    """R1: `show --rebuild-md` needs an agent id, commits keche.md through
    the shared helper (author = agent id, clean tree after), and the next
    write command must not see crash_residue. Plain `show` stays
    read-only and needs no agent id."""
    t1 = _add_task(new_project, "Do work")

    plain = run_kechup(["show", t1], cwd=new_project)
    assert plain.exit_code == 0, plain.stderr

    no_agent = run_kechup(["show", "--rebuild-md"], cwd=new_project)
    assert no_agent.exit_code == 3
    assert no_agent.json["error"]["code"] == "no_agent_id"

    before_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    rebuild = run_kechup(["show", "--rebuild-md"], cwd=new_project, agent="rebuilder")
    assert rebuild.exit_code == 0, rebuild.stderr
    after_head = git(new_project, "rev-parse", "HEAD").stdout.strip()
    assert after_head != before_head, "rebuild-md must commit keche.md"

    author = git(new_project, "show", "-s", "--format=%an", "HEAD").stdout.strip()
    assert author == "rebuilder"

    status = git(new_project, "status", "--porcelain").stdout.strip()
    assert status == "", f"working tree must be clean after rebuild: {status!r}"

    claim = run_kechup(["claim", t1], cwd=new_project, agent="agent-a")
    assert claim.exit_code == 0, claim.stderr


def test_snapshot_handles_new_folder_deletion_rename_and_special_names(
    new_project: Path,
) -> None:
    """R2: `snapshot` must pick up every file inside a brand-new folder
    (not just report the folder), commit a tracked deletion, follow a
    rename, and handle a name with a space and a non-ASCII letter."""
    tracked = new_project / "tracked.txt"
    renamed_from = new_project / "oldname.txt"
    tracked.write_text("will be deleted")
    renamed_from.write_text("will be renamed")
    first = run_kechup(["snapshot"], cwd=new_project, agent="a")
    assert first.exit_code == 0, first.stderr
    assert "tracked.txt" in first.json["data"]["paths"]
    assert "oldname.txt" in first.json["data"]["paths"]

    nested_dir = new_project / "newfolder" / "deeper"
    nested_dir.mkdir(parents=True)
    (nested_dir / "inside.txt").write_text("hi")
    tracked.unlink()
    git_as(new_project, "a", "mv", "oldname.txt", "newname.txt")

    special = new_project / "café ñ.txt"
    special.write_text("hola")

    result = run_kechup(["snapshot"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    paths = result.json["data"]["paths"]
    assert "newfolder/deeper/inside.txt" in paths
    assert "tracked.txt" in paths
    assert "café ñ.txt" in paths
    status_after = git(new_project, "status", "--porcelain").stdout
    assert status_after.strip() == "", f"snapshot must leave a clean tree: {status_after!r}"
    tracked_files = git(new_project, "ls-files").stdout
    assert "tracked.txt" not in tracked_files
    assert "newname.txt" in tracked_files
    assert "oldname.txt" not in tracked_files.splitlines()


def test_snapshot_commits_only_eligible_files(new_project: Path) -> None:
    """T4 (R2 skip-and-report): an oversized or secret-like file is
    skipped and listed, never refused; the other eligible file is still
    committed, exit 0."""
    (new_project / "eligible.txt").write_text("hi")
    (new_project / "big.bin").write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    (new_project / "id_rsa").write_text("secret")
    result = run_kechup(["snapshot"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["committed"] is True
    assert "eligible.txt" in result.json["data"]["paths"]
    assert "big.bin" not in result.json["data"]["paths"]
    assert "id_rsa" not in result.json["data"]["paths"]
    skipped = {item["path"]: item for item in result.json["data"]["skipped"]}
    assert skipped["big.bin"]["reason"] == "over 5 MB"
    assert skipped["big.bin"]["bytes"] == 5 * 1024 * 1024 + 1
    assert skipped["id_rsa"]["reason"] == "secret-like"
    status = git(new_project, "status", "--porcelain").stdout
    assert "big.bin" in status
    assert "id_rsa" in status


def test_snapshot_accepts_by_and_keeps_snapshot_author(new_project: Path) -> None:
    """An agent that passes --by on every write command can also pass it
    to snapshot. The commit author stays "snapshot"."""
    (new_project / "notes.txt").write_text("hi")
    result = run_kechup(
        ["snapshot", "--by", "keche:session:x"], cwd=new_project, agent=None
    )
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["committed"] is True
    author = git(new_project, "log", "-1", "--format=%an").stdout.strip()
    assert author == "snapshot"


def test_config_set_owner_only_current_owner(new_project: Path) -> None:
    result = run_kechup(["config", "set-owner", "new-owner"], cwd=new_project, agent="not-owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "not_owner"
    ok = run_kechup(["config", "set-owner", "new-owner"], cwd=new_project, agent="owner")
    assert ok.exit_code == 0, ok.stderr
    config = json.loads((new_project / ".kechup" / "config.json").read_text())
    assert config["owner_id"] == "new-owner"


def test_json_flag_works_after_nested_subcommand(new_project: Path) -> None:
    """Section 5 says every command accepts `--json`. A command with a
    nested subcommand (`add task`, `dep add`, `dep rm`, `config
    set-owner`) must accept `--json` placed after its full command
    name, not only before it as a global flag."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    env["KECHUP_AGENT"] = "a"

    def run_with_trailing_json(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "kechup", *args, "--json"],
            cwd=str(new_project),
            env=env,
            capture_output=True,
            text=True,
            timeout=20.0,
        )

    added = run_with_trailing_json(["add", "task", "x"])
    assert added.returncode == 0, added.stderr
    task_id = json.loads(added.stdout)["data"]["id"]

    second = run_with_trailing_json(["add", "task", "y"])
    assert second.returncode == 0, second.stderr
    second_id = json.loads(second.stdout)["data"]["id"]

    dep_added = run_with_trailing_json(["dep", "add", second_id, "--after", task_id])
    assert dep_added.returncode == 0, dep_added.stderr
    assert json.loads(dep_added.stdout)["ok"] is True

    dep_removed = run_with_trailing_json(["dep", "rm", second_id, task_id])
    assert dep_removed.returncode == 0, dep_removed.stderr
    assert json.loads(dep_removed.stdout)["ok"] is True

    owner_env = dict(env)
    owner_env["KECHUP_AGENT"] = "owner"
    set_owner = subprocess.run(
        [sys.executable, "-m", "kechup", "config", "set-owner", "new-owner", "--json"],
        cwd=str(new_project),
        env=owner_env,
        capture_output=True,
        text=True,
        timeout=20.0,
    )
    assert set_owner.returncode == 0, set_owner.stderr
    assert json.loads(set_owner.stdout)["ok"] is True
