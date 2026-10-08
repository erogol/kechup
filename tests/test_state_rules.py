"""State rule tests (spec section 10, "State rules"): one test per
transition-table row, one per displayed-state rule, plus the two layering
tests."""

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


def _state_of(project: Path, task_id: str) -> str:
    result = run_kechup(["show", task_id], cwd=project, agent="x")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["state"]


# --- Transition table rows ---


def test_transition_new_to_todo(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    assert _state_of(new_project, t1) == "todo"


def test_transition_todo_to_doing_via_claim(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    assert _state_of(new_project, t1) == "doing"


def test_transition_stale_doing_to_doing_via_claim(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    _backdate_claim(new_project, t1)
    result = run_kechup(["claim", t1], cwd=new_project, agent="b")
    assert result.exit_code == 0, result.stderr
    assert _state_of(new_project, t1) == "doing"


def test_transition_todo_to_doing_via_next_claim(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    result = run_kechup(["next", "--claim"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["id"] == t1
    assert _state_of(new_project, t1) == "doing"


def test_transition_doing_to_todo_via_unclaim(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    run_kechup(["unclaim", t1], cwd=new_project, agent="a")
    assert _state_of(new_project, t1) == "todo"


def test_transition_doing_stays_doing_via_note_renews_claim(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    before = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    claimed_before = before["tasks"][t1]["claimed_at"]
    result = run_kechup(["note", t1, "progress"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stderr
    after = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    assert after["tasks"][t1]["state"] == "doing"
    assert after["tasks"][t1]["claimed_at"] >= claimed_before


def test_transition_any_nonterminal_to_doing_via_assign(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    result = run_kechup(["assign", t1, "--to", "agent-z"], cwd=new_project, agent="any")
    assert result.exit_code == 0, result.stderr
    assert _state_of(new_project, t1) == "doing"


def test_transition_block_display_only(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    run_kechup(["block", t1, "--reason", "waiting"], cwd=new_project, agent="any")
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    # Stored state is unchanged; only display flips.
    assert tasks_json["tasks"][t1]["state"] == "doing"
    assert _state_of(new_project, t1) == "blocked"


def test_transition_unblock_display_reverts(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["block", t1, "--reason", "waiting"], cwd=new_project, agent="any")
    run_kechup(["unblock", t1], cwd=new_project, agent="any")
    assert _state_of(new_project, t1) == "todo"


def test_transition_any_nonterminal_to_dropped(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    result = run_kechup(["drop", t1, "--reason", "no longer needed"], cwd=new_project, agent="any")
    assert result.exit_code == 0, result.stderr
    assert _state_of(new_project, t1) == "dropped"


def test_transition_any_nonterminal_to_done_owner_only(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    _complete_task(new_project, t1)
    assert _state_of(new_project, t1) == "done"


def test_transition_done_to_todo_via_reopen(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    _complete_task(new_project, t1)
    result = run_kechup(["reopen", t1, "--reason", "redo"], cwd=new_project, agent="any")
    assert result.exit_code == 0, result.stderr
    assert _state_of(new_project, t1) == "todo"


# --- Displayed-state rules (section 4, checked in order) ---


def test_displayed_dropped(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["drop", t1, "--reason", "r"], cwd=new_project, agent="a")
    assert _state_of(new_project, t1) == "dropped"


def test_displayed_done_no_flag(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    _complete_task(new_project, t1)
    assert _state_of(new_project, t1) == "done"


def test_displayed_done_with_flag(new_project: Path) -> None:
    """T7: assert the complete flag text, not only a prefix."""
    t1 = _add_task(new_project, "x")
    _complete_task(new_project, t1)
    _revert_done_commit(new_project, t1)
    revert_commit = git(new_project, "rev-parse", "HEAD").stdout.strip()
    short = git(new_project, "rev-parse", "--short", revert_commit).stdout.strip()
    assert _state_of(new_project, t1) == f"done — WORK REVERTED by {short} (reverter)"


def test_displayed_blocked_manual_reason(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["block", t1, "--reason", "needs input"], cwd=new_project, agent="a")
    result = run_kechup(["show", t1], cwd=new_project, agent="a")
    assert result.json["data"]["blocker_cause"] == "needs input"


def test_displayed_blocked_after_dropped(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    run_kechup(["drop", t1, "--reason", "r"], cwd=new_project, agent="a")
    result = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert "waits for dropped" in result.json["data"]["blocker_cause"]


def test_displayed_blocked_after_not_done(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    result = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert result.json["data"]["blocker_cause"] == f"waits for {t1}"


def test_displayed_blocked_after_flagged_done(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    _complete_task(new_project, t1)
    _revert_done_commit(new_project, t1)
    result = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert "work reverted" in result.json["data"]["blocker_cause"]


def test_displayed_stale_doing(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    run_kechup(["claim", t1], cwd=new_project, agent="a")
    _backdate_claim(new_project, t1)
    assert _state_of(new_project, t1) == "doing (stale)"


def test_displayed_plain_stored_state(new_project: Path) -> None:
    t1 = _add_task(new_project, "x")
    assert _state_of(new_project, t1) == "todo"


# --- Layering tests ---


def test_layering_manual_block_over_dependency_block(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    run_kechup(["block", t2, "--reason", "manual cause"], cwd=new_project, agent="a")
    result = run_kechup(["show", t2], cwd=new_project, agent="a")
    # Section 4 checks block_reason (step 3) before `after` (step 4): the
    # manual reason wins even though a dependency block would also apply.
    assert result.json["data"]["blocker_cause"] == "manual cause"


def test_layering_dependency_block_over_flagged_done_after(new_project: Path) -> None:
    t1 = _add_task(new_project, "First")
    t2 = _add_task(new_project, "Second", after=t1)
    _complete_task(new_project, t1)
    _revert_done_commit(new_project, t1)
    result = run_kechup(["show", t2], cwd=new_project, agent="a")
    assert result.json["data"]["state"] == "blocked"
    assert "work reverted" in result.json["data"]["blocker_cause"]


# --- Helpers ---


def _backdate_claim(project: Path, task_id: str) -> None:
    tasks_json = project / ".kechup" / "tasks.json"
    data = json.loads(tasks_json.read_text())
    data["tasks"][task_id]["claimed_at"] = "2000-01-01T00:00:00+00:00"
    tasks_json.write_text(json.dumps(data))
    git(project, "add", ".kechup/tasks.json")
    git_as(project, "owner", "commit", "-q", "-m", "backdate for test")


def _complete_task(project: Path, task_id: str) -> None:
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


def _revert_done_commit(project: Path, task_id: str) -> None:
    tasks_json = json.loads((project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][task_id]["done_commit"]
    git_as(project, "reverter", "revert", "--no-edit", done_commit)
