"""R7: `load_tasks`/`load_config` must validate the full SPEC section 3
schema, not just presence of a `version` key. Exit 3, code `invalid_tasks`
or `invalid_config`, naming the first problem found."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git, git_as, run_kechup


def _write_tasks(project: Path, data: object) -> None:
    (project / ".kechup" / "tasks.json").write_text(json.dumps(data))


def test_tasks_version_only_is_invalid(new_project: Path) -> None:
    _write_tasks(new_project, {"version": 1})
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"


def test_tasks_scalar_value_is_invalid(new_project: Path) -> None:
    """A scalar tasks.json also makes `is_kechup_managed` say "not a
    project" (section 5 gate, checked ahead of model.py), so the CLI
    reports `not_a_project`. The loader itself is tested directly here."""
    (new_project / ".kechup" / "tasks.json").write_text("42")
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "not_a_project"

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kechup.errors import KechupError
    from kechup.model import load_tasks

    try:
        load_tasks(new_project)
        raise AssertionError("load_tasks must refuse a scalar tasks.json")
    except KechupError as exc:
        assert exc.code == "invalid_tasks"


def test_tasks_unknown_state_is_invalid(new_project: Path) -> None:
    _write_tasks(
        new_project,
        {
            "version": 1,
            "next_id": 2,
            "tasks": {
                "T1": {
                    "id": "T1",
                    "title": "x",
                    "state": "blocked",
                    "folder": "tasks/T1-x",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "created_by": "owner",
                }
            },
        },
    )
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"


def test_tasks_missing_field_is_invalid(new_project: Path) -> None:
    _write_tasks(
        new_project,
        {
            "version": 1,
            "next_id": 2,
            "tasks": {
                "T1": {
                    "id": "T1",
                    "title": "x",
                    "state": "todo",
                    "folder": "tasks/T1-x",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    # created_by missing
                }
            },
        },
    )
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"


def test_tasks_wrong_type_is_invalid(new_project: Path) -> None:
    _write_tasks(
        new_project,
        {
            "version": 1,
            "next_id": 2,
            "tasks": {
                "T1": {
                    "id": "T1",
                    "title": "x",
                    "state": "todo",
                    "folder": "tasks/T1-x",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "created_by": "owner",
                    "priority": "high",
                }
            },
        },
    )
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"


def test_tasks_unknown_after_id_is_invalid(new_project: Path) -> None:
    _write_tasks(
        new_project,
        {
            "version": 1,
            "next_id": 2,
            "tasks": {
                "T1": {
                    "id": "T1",
                    "title": "x",
                    "state": "todo",
                    "folder": "tasks/T1-x",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "created_by": "owner",
                    "after": ["T9"],
                }
            },
        },
    )
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"


def _add_task(project: Path) -> str:
    result = run_kechup(["add", "task", "x"], cwd=project, agent="owner")
    assert result.exit_code == 0, result.stderr
    return result.json["data"]["id"]


def _commit_config(project: Path, text: str) -> None:
    """Writes config.json and commits it, so the dirty-metadata guard does
    not fire before load_config runs."""
    (project / ".kechup" / "config.json").write_text(text)
    git(project, "add", ".kechup/config.json")
    git_as(project, "owner", "commit", "-q", "-m", "test: corrupt config.json")


def test_config_scalar_value_is_invalid(new_project: Path) -> None:
    t1 = _add_task(new_project)
    _commit_config(new_project, "7")
    result = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_config"


def test_config_missing_field_is_invalid(new_project: Path) -> None:
    t1 = _add_task(new_project)
    _commit_config(new_project, json.dumps({"version": 1, "owner_id": "owner"}))
    result = run_kechup(["done", t1, "--evidence", "ev"], cwd=new_project, agent="owner")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_config"
