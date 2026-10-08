"""Finds the project root and checks whether a folder is kechup-managed
(spec section 5)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kechup import gitutil
from kechup.errors import KechupError


def find_project_root(start: Path) -> Path:
    """Walks up from `start` to find a kechup-managed project root."""
    toplevel = gitutil.repo_toplevel(start)
    if toplevel is None:
        raise KechupError(
            3, "not_a_project", "not a kechup project: run from inside a project folder"
        )
    if not is_kechup_managed(toplevel):
        raise KechupError(
            3, "not_a_project", "not a kechup project: run from inside a project folder"
        )
    return toplevel


def is_kechup_managed(folder: Path) -> bool:
    """A folder is kechup-managed only when: its root is a git working tree,
    `.kechup/tasks.json` parses with a `version` key, and a commit with
    `Kechup-Init: 1` exists (section 5)."""
    if not gitutil.is_git_repo(folder):
        return False
    tasks_json = folder / ".kechup" / "tasks.json"
    if not tasks_json.exists():
        return False
    try:
        import json

        data = json.loads(tasks_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or "version" not in data:
        return False
    result = subprocess.run(
        ["git", "log", "--grep=^Kechup-Init: 1$", "--format=%H"],
        cwd=str(folder),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())
