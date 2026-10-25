"""Write-ahead intent journal: the evidence for crash-residue
automatic recovery (CRASH_RECOVERY_SPEC v2).

A kechup write command records its full intended transaction under the
project lock, before it touches the first guarded file. If the process
dies before the commit, the journal plus the file state prove a
complete, kechup-owned transaction, and the next write command commits
it. Anything ambiguous, invalid, foreign, or partially written stays
refused (fail-closed)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from kechup import gitutil
from kechup.errors import KechupError
from kechup.model import COMMIT_ID_RE

_JOURNAL_KEYS = {"version", "base_head", "agent", "message", "paths"}

JOURNAL_VERSION = 1
JOURNAL_NAME = "kechup-journal.json"

# The metadata files kechup owns and guards (same set as guard.py).
GUARDED_PATHS = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]


def journal_path(project_root: Path) -> Path:
    """The journal lives inside the git dir: never tracked, invisible
    to `git status`, one per repository."""
    return gitutil.git_dir(project_root) / JOURNAL_NAME


def record(
    project_root: Path, agent_id: str, message: str, paths: dict[str, str]
) -> None:
    """Publish the intent journal. The caller holds the project lock
    and has passed the crash guard, so no journal can be present;
    a present journal is residue and must never be overwritten."""
    path = journal_path(project_root)
    if path.exists():
        raise KechupError(
            3,
            "crash_residue",
            "an intent journal is already present; run `kechup repair`",
        )
    if not agent_id:
        raise KechupError(3, "git_failure", "journal record requires an agent id")
    if not message:
        raise KechupError(3, "git_failure", "journal record requires a commit message")
    foreign = sorted(set(paths) - set(GUARDED_PATHS))
    if not paths or foreign:
        raise KechupError(
            3, "git_failure", f"journal paths must be guarded paths, got: {foreign}"
        )

    data = {
        "version": JOURNAL_VERSION,
        "base_head": gitutil.head_commit(project_root),
        "agent": agent_id,
        "message": message,
        "paths": dict(paths),
    }
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def clear(project_root: Path) -> None:
    """Remove the journal. Safe when absent."""
    try:
        journal_path(project_root).unlink()
    except FileNotFoundError:
        pass


def _load(path: Path) -> dict[str, Any] | None:
    """Parse and strictly validate the journal. None on any deviation."""

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate JSON key")
        return dict(pairs)

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != _JOURNAL_KEYS:
        return None
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    if version != JOURNAL_VERSION:
        return None
    base_head = data["base_head"]
    if not isinstance(base_head, str) or not COMMIT_ID_RE.match(base_head):
        return None
    if not isinstance(data["agent"], str) or not data["agent"]:
        return None
    if not isinstance(data["message"], str) or not data["message"]:
        return None
    paths = data["paths"]
    if not isinstance(paths, dict) or not paths:
        return None
    for rel, content in paths.items():
        if rel not in GUARDED_PATHS or not isinstance(content, str):
            return None
    return data


def _worktree_file_safe(project_root: Path, rel: str, intended: bytes) -> bool:
    """The on-disk file must be a regular, non-executable file whose
    bytes equal the intended bytes. A symlink (also in a parent
    directory), a directory, or a mode change refuses."""
    current = project_root
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return False
    try:
        st = os.lstat(current)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if stat.S_IMODE(st.st_mode) & 0o111:
        return False
    try:
        return current.read_bytes() == intended
    except OSError:
        return False


def _index_safe(project_root: Path, rel: str, intended: bytes) -> bool:
    """A staged index entry is safe only when it is our own add (equals
    the intended bytes) or unchanged versus HEAD. A foreign staged
    blob, or a foreign type/mode (symlink, gitlink, executable), is not."""
    entry = gitutil.index_entry(project_root, rel)
    if entry is None:
        # No index entry. A kechup crash before `git add` leaves the
        # old HEAD entry in the index, so entry is None only after a
        # foreign `git rm --cached`. Refuse, unless the path is also
        # absent at HEAD: a new kechup file killed before its first
        # `git add` has no entry and no HEAD blob.
        return gitutil.head_blob_content_bytes(project_root, rel) is None
    mode, sha = entry
    if mode != "100644":
        return False
    staged = gitutil.blob_content_bytes(project_root, sha)
    if staged == intended:
        return True
    head_content = gitutil.head_blob_content_bytes(project_root, rel)
    return head_content is not None and staged == head_content


def recover(project_root: Path) -> bool:
    """Try to complete the interrupted transaction recorded in the
    journal. True when the residue was committed and the journal
    cleared. False when the evidence does not prove a complete,
    kechup-owned transaction; the caller keeps the fail-closed refusal
    and the journal stays on disk as evidence."""
    path = journal_path(project_root)
    if not path.exists():
        return False
    data = _load(path)
    if data is None:
        return False
    if data["base_head"] != gitutil.head_commit(project_root):
        return False
    intended = {rel: content.encode("utf-8") for rel, content in data["paths"].items()}
    for rel, content in intended.items():
        if not _worktree_file_safe(project_root, rel, content):
            # Partial or foreign write, wrong file type, or mode
            # change: never completed, never replayed.
            return False
    dirty = gitutil.dirty_paths(project_root, GUARDED_PATHS)
    for rel in dirty:
        if rel not in intended:
            return False
    for rel, content in intended.items():
        if not _index_safe(project_root, rel, content):
            return False
    ordered = sorted(intended)
    gitutil.git_add(project_root, ordered)
    gitutil.commit(project_root, data["agent"], data["message"], paths=ordered, only=True)
    clear(project_root)
    return True