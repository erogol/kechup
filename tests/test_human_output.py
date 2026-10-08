"""Tests for the F3 fix: human text output (`_print_human` in
`kechup/cli.py`) must not print a Python repr. `--json` output is covered
by the other test files and must stay unchanged."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git_as, run_kechup

REPR_PATTERNS = ("{'", "['", "': '", '"}')


def _add_task(project: Path, title: str, **kwargs: str) -> str:
    args = ["add", "task", title]
    for key, value in kwargs.items():
        args.extend([f"--{key.replace('_', '-')}", value])
    result = run_kechup(args, cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def _complete_task(project: Path, task_id: str) -> None:
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


def test_log_text_output_one_line_per_entry_no_repr(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    _complete_task(new_project, t1)
    tasks_json = json.loads((new_project / ".kechup" / "tasks.json").read_text())
    done_commit = tasks_json["tasks"][t1]["done_commit"]
    git_as(new_project, "reverter", "revert", "--no-edit", done_commit)

    result = run_kechup(["log", t1], cwd=new_project, agent="a", as_json=False)
    assert result.exit_code == 0, result.stderr
    for pattern in REPR_PATTERNS:
        assert pattern not in result.stdout

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) >= 2
    for line in lines:
        columns = [c for c in line.split("  ") if c]
        assert len(columns) >= 4


def test_show_text_output_no_repr(new_project: Path) -> None:
    t1 = _add_task(new_project, "Do work")
    t2 = _add_task(new_project, "Blocked work", after=t1)
    claim_result = run_kechup(["claim", t1], cwd=new_project, agent="a")
    assert claim_result.exit_code == 0, claim_result.stderr
    note_result = run_kechup(["note", t1, "making progress"], cwd=new_project, agent="a")
    assert note_result.exit_code == 0, note_result.stderr

    result = run_kechup(["show", t1], cwd=new_project, agent="a", as_json=False)
    assert result.exit_code == 0, result.stderr
    for pattern in REPR_PATTERNS:
        assert pattern not in result.stdout
    assert "notes:" in result.stdout
    assert "making progress" in result.stdout

    blocked_result = run_kechup(["show", t2], cwd=new_project, agent="a", as_json=False)
    assert blocked_result.exit_code == 0, blocked_result.stderr
    for pattern in REPR_PATTERNS:
        assert pattern not in blocked_result.stdout
    assert "after:" in blocked_result.stdout
    assert t1 in blocked_result.stdout


def test_human_output_preserves_repr_like_title_and_note_unchanged(new_project: Path) -> None:
    weird_title = "[x] {'a': 1}"
    weird_note = "note with [y] {'b': 2} text"
    t1 = _add_task(new_project, weird_title)
    claim_result = run_kechup(["claim", t1], cwd=new_project, agent="a")
    assert claim_result.exit_code == 0, claim_result.stderr
    note_result = run_kechup(["note", t1, weird_note], cwd=new_project, agent="a")
    assert note_result.exit_code == 0, note_result.stderr

    result = run_kechup(["show", t1], cwd=new_project, agent="a", as_json=False)
    assert result.exit_code == 0, result.stderr
    assert weird_title in result.stdout
    assert weird_note in result.stdout
