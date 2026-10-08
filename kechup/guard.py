"""The dirty-metadata crash guard (spec section 6). Runs right after the
project lock, before the revert-logging prelude, for every write command
except `repair`, `new`, `adopt`."""

from __future__ import annotations

from pathlib import Path

from kechup import gitutil
from kechup.errors import KechupError

GUARDED_PATHS = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]


def check_no_crash_residue(project_root: Path) -> None:
    if gitutil.working_tree_dirty(project_root, GUARDED_PATHS):
        raise KechupError(
            3,
            "crash_residue",
            "found uncommitted changes from an earlier crash; run `kechup repair` "
            "to commit them, or `git checkout -- <path>` to discard them",
        )
