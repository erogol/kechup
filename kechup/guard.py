"""The dirty-metadata crash guard (spec section 6). Runs right after the
project lock, before the revert-logging prelude, for every write command
except `repair`, `new`, `adopt`.

With automatic recovery (CRASH_RECOVERY_SPEC v2): a dirty residue from
a proven, complete, kechup-owned transaction is committed by the guard
itself; every other residue stays refused, fail-closed."""

from __future__ import annotations

from pathlib import Path

from kechup import gitutil, journal
from kechup.errors import KechupError
from kechup.journal import GUARDED_PATHS


def crash_residue_error() -> KechupError:
    return KechupError(
        3,
        "crash_residue",
        "found uncommitted changes from an earlier crash; run `kechup repair` "
        "to commit them, or `git checkout -- <path>` to discard them",
    )


def check_no_crash_residue(project_root: Path) -> None:
    if gitutil.working_tree_dirty(project_root, GUARDED_PATHS):
        # Dirty tree: recover only with journal proof of a complete,
        # kechup-owned transaction; refuse everything else.
        if not journal.recover(project_root):
            raise crash_residue_error()
    else:
        # Clean tree with a leftover journal: the transaction landed
        # fully or never touched a guarded file. No data remains to
        # recover; drop the stale evidence.
        journal.clear(project_root)