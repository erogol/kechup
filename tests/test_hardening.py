"""Hardening: commit ids that reach git are full hex ids, the shared-temp
lock folder must be private, and a foreign prepare-commit-msg hook is kept."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import git, run_kechup

from kechup import gitutil, lock
from kechup.errors import KechupError


def _tasks(project: Path) -> dict:
    return json.loads((project / ".kechup" / "tasks.json").read_text())


def test_done_commit_option_like_value_is_invalid(new_project: Path) -> None:
    result = run_kechup(["add", "task", "First"], cwd=new_project, agent="a")
    assert result.exit_code == 0, result.stdout
    data = _tasks(new_project)
    task = next(iter(data["tasks"].values()))
    task["state"] = "done"
    task["done_commit"] = "--output=/tmp/x"
    (new_project / ".kechup" / "tasks.json").write_text(json.dumps(data))
    result = run_kechup(["list"], cwd=new_project, agent="a")
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "invalid_tasks"
    assert "done_commit" in result.json["error"]["message"]


def test_revert_trailer_with_bad_value_is_ignored(new_project: Path) -> None:
    (new_project / "a.txt").write_text("a\n")
    git(new_project, "add", "a.txt")
    git(new_project, "commit", "-q", "-m", "x\n\nKechup-Reverts: --all")
    head = gitutil.head_commit(new_project)
    assert gitutil.revert_target(new_project, head) is None


def test_lock_dir_owned_by_this_user_is_accepted(tmp_path: Path) -> None:
    lock_dir = tmp_path / "locks"
    lock._ensure_private_dir(lock_dir)
    assert lock_dir.is_dir()
    assert (lock_dir.stat().st_mode & 0o077) == 0
    lock._ensure_private_dir(lock_dir)


def test_lock_dir_writable_by_others_is_refused(tmp_path: Path) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    os.chmod(lock_dir, 0o777)
    with pytest.raises(KechupError) as exc:
        lock._ensure_private_dir(lock_dir)
    assert exc.value.code == "unsafe_lock_dir"


def test_lock_dir_symlink_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "locks"
    link.symlink_to(real)
    with pytest.raises(KechupError) as exc:
        lock._ensure_private_dir(link)
    assert exc.value.code == "unsafe_lock_dir"


def test_default_lock_path_uses_private_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KECHUP_LOCK_DIR", raising=False)
    monkeypatch.setattr(lock.tempfile, "gettempdir", lambda: str(tmp_path))
    path = lock.bootstrap_lock_path(tmp_path / "proj")
    assert path.parent == tmp_path / f"kechup-locks-{os.getuid()}"
    assert (path.parent.stat().st_mode & 0o077) == 0


def _repo_with_hook(tmp_dir: Path, body: str | None, link: bool = False) -> Path:
    target = tmp_dir / "proj"
    target.mkdir()
    git(target, "init", "-q")
    hooks = target / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "prepare-commit-msg"
    if link:
        real = tmp_dir / "real-hook"
        real.write_text(gitutil.HOOK_SCRIPT)
        hook.symlink_to(real)
    elif body is not None:
        hook.write_text(body)
    (target / "keche.md").write_text("# proj\n")
    return target


def test_adopt_refuses_foreign_hook(tmp_dir: Path) -> None:
    target = _repo_with_hook(tmp_dir, "#!/bin/sh\necho mine\n")
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="a")
    assert result.exit_code == 2, result.stdout
    assert result.json["error"]["code"] == "foreign_hook"
    hook = target / ".git" / "hooks" / "prepare-commit-msg"
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    assert not (target / ".kechup").exists()


def test_adopt_refuses_symlink_hook(tmp_dir: Path) -> None:
    target = _repo_with_hook(tmp_dir, None, link=True)
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="a")
    assert result.exit_code == 2, result.stdout
    assert result.json["error"]["code"] == "foreign_hook"
    assert (tmp_dir / "real-hook").read_text() == gitutil.HOOK_SCRIPT


def test_kechup_hook_is_rewritten(tmp_dir: Path) -> None:
    old = gitutil.HOOK_MARKER + "\n# older kechup body\n"
    target = _repo_with_hook(tmp_dir, "#!/bin/sh\n" + old)
    gitutil.install_hook(target)
    assert gitutil.hook_present_and_current(target)
