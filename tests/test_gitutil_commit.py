"""R10: `gitutil.commit` must never commit the whole index. `paths` must
be explicit and non-empty, except the `--only --allow-empty` marker
commit with no paths."""

from __future__ import annotations

from pathlib import Path

from conftest import git

from kechup import gitutil
from kechup.errors import KechupError


def test_commit_without_paths_and_without_only_allow_empty_refuses(tmp_dir: Path) -> None:
    repo = tmp_dir / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--initial-branch=main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    try:
        gitutil.commit(repo, "owner", "whole index commit", paths=None)
        raise AssertionError("commit must refuse paths=None without --only --allow-empty")
    except KechupError as exc:
        assert exc.code == "git_failure"
    status = git(repo, "status", "--porcelain").stdout
    assert "A  a.txt" in status, "the staged file must remain staged, uncommitted"


def test_commit_empty_paths_list_refuses(tmp_dir: Path) -> None:
    repo = tmp_dir / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--initial-branch=main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    try:
        gitutil.commit(repo, "owner", "empty paths", paths=[])
        raise AssertionError("commit must refuse an empty paths list")
    except KechupError as exc:
        assert exc.code == "git_failure"


def test_commit_only_allow_empty_with_no_paths_is_still_allowed(tmp_dir: Path) -> None:
    """The one documented exception (used by the revert-log marker commit)."""
    repo = tmp_dir / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--initial-branch=main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    commit_id = gitutil.commit(
        repo, "owner", "marker commit", paths=None, allow_empty=True, only=True
    )
    assert len(commit_id) == 40
