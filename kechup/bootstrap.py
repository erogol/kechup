"""`new`, `adopt`, and `repair` (spec section 5): bootstrap and crash
recovery."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from kechup import gitutil
from kechup.errors import KechupError
from kechup.kechemd_template import render_template
from kechup.lock import bootstrap_lock, project_lock
from kechup.model import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_OWNER_ID,
    TASKS_SCHEMA_VERSION,
    TasksFile,
    config_json_path,
    load_config,
    load_tasks,
    save_config,
    save_tasks,
    tasks_json_path,
)
from kechup.project import is_kechup_managed
from kechup.revertlog import run_revert_logging_prelude
from kechup.safety import is_secret_like, is_too_large
from kechup.timeutil import now_iso
from kechup.worksection import rewrite_keche_md

GITIGNORE_TEMPLATE = """\
*.mp4
*.wav
*.flac
*.ckpt
*.pth
*.safetensors
venv/
.venv/
secrets/
.env
.kechup/lock
"""

BOOTSTRAP_FILE_NAMES = {".gitignore", "keche.md"}
BOOTSTRAP_DIR_NAMES = {".kechup", ".git"}


@dataclass
class UnsafeFile:
    """One refused file (FIX_SPEC F1)."""

    path: str
    reason: str  # "over 5 MB" or "secret-like"
    size_bytes: int
    tracked: bool
    ignored_by_rule: bool


def _is_in_nested_repo(folder: Path, rel: str) -> bool:
    """True when `rel` sits inside a subfolder that has its own `.git`."""
    path = folder / rel
    for parent in path.parents:
        if parent == folder:
            break
        if (parent / ".git").exists():
            return True
    return False


def _project_ignore_scope(folder: Path) -> tuple[set[str], set[str], Path]:
    """FIX_SPEC F1 ignore policy: project `.gitignore` files (all levels)
    apply, plus the default template only when the target has no top
    `.gitignore`. Global excludes and the target's own `.git/info/exclude`
    never apply. Returns (untracked-and-not-ignored paths, tracked paths,
    the temp git dir — caller must remove it)."""
    tmp_git_dir = Path(tempfile.mkdtemp(prefix="kechup-scan-"))
    init_result = subprocess.run(
        ["git", f"--git-dir={tmp_git_dir}", "init", "-q"],
        cwd=str(folder),
        capture_output=True,
        text=True,
    )
    if init_result.returncode != 0:
        shutil.rmtree(tmp_git_dir, ignore_errors=True)
        raise KechupError(3, "git_error", f"git init failed: {init_result.stderr.strip()}")
    if not (folder / ".gitignore").exists():
        info_dir = tmp_git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        (info_dir / "exclude").write_text(GITIGNORE_TEMPLATE, encoding="utf-8")

    others = subprocess.run(
        [
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            f"--git-dir={tmp_git_dir}",
            f"--work-tree={folder}",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=str(folder),
        capture_output=True,
        text=True,
    )
    if others.returncode != 0:
        shutil.rmtree(tmp_git_dir, ignore_errors=True)
        raise KechupError(
            3, "git_error", f"git ls-files --others failed: {others.stderr.strip()}"
        )
    not_ignored = {p for p in others.stdout.split("\0") if p}

    tracked: set[str] = set()
    if (folder / ".git").exists():
        tracked_result = subprocess.run(
            ["git", "-C", str(folder), "ls-files", "-z"],
            capture_output=True,
            text=True,
        )
        if tracked_result.returncode != 0:
            shutil.rmtree(tmp_git_dir, ignore_errors=True)
            raise KechupError(
                3, "git_error", f"git ls-files failed: {tracked_result.stderr.strip()}"
            )
        tracked = {p for p in tracked_result.stdout.split("\0") if p}

    return not_ignored, tracked, tmp_git_dir


def _is_ignored_by_project_rules(folder: Path, tmp_git_dir: Path, rel: str) -> bool:
    """Whether `rel` would be excluded by the project's own ignore rules
    (used to decide the tracked-file advice text, FIX_SPEC F1)."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            f"--git-dir={tmp_git_dir}",
            f"--work-tree={folder}",
            "check-ignore",
            "-q",
            "--",
            rel,
        ],
        cwd=str(folder),
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise KechupError(
            3, "git_error", f"git check-ignore failed: {result.stderr.strip()}"
        )
    return result.returncode == 0


def _scan_for_unsafe_files(folder: Path) -> list[UnsafeFile]:
    """Step 3 (FIX_SPEC F1): scan only the files in scope under the
    project-only ignore policy for the 5 MB / secret-like checks. A tracked
    file is always in scope, even when an ignore rule matches it."""
    not_ignored, tracked, tmp_git_dir = _project_ignore_scope(folder)
    try:
        in_scope = {
            rel
            for rel in (not_ignored | tracked)
            if rel
            and rel.split("/")[0] != ".git"
            and not _is_in_nested_repo(folder, rel)
        }
        problems: list[UnsafeFile] = []
        for rel in sorted(in_scope):
            full = folder / rel
            if not full.is_file():
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            is_tracked = rel in tracked
            if is_secret_like(Path(rel)):
                reason = "secret-like"
            elif is_too_large(full):
                reason = "over 5 MB"
            else:
                continue
            ignored_by_rule = (
                _is_ignored_by_project_rules(folder, tmp_git_dir, rel) if is_tracked else False
            )
            problems.append(
                UnsafeFile(
                    path=rel,
                    reason=reason,
                    size_bytes=size,
                    tracked=is_tracked,
                    ignored_by_rule=ignored_by_rule,
                )
            )
        return problems
    finally:
        shutil.rmtree(tmp_git_dir, ignore_errors=True)


def _unsafe_file_error(problems: list[UnsafeFile]) -> KechupError:
    """Builds the structured refusal (FIX_SPEC F1): one line per file, plus
    advice. kechup never untracks a file itself."""
    file_lines = []
    for p in problems:
        tracked_part = ", tracked" if p.tracked else ""
        file_lines.append(f"  {p.path}: {p.reason}, {p.size_bytes} bytes{tracked_part}")

    any_untracked = any(not p.tracked for p in problems)
    any_tracked_ignored = any(p.tracked and p.ignored_by_rule for p in problems)
    any_tracked_not_ignored = any(p.tracked and not p.ignored_by_rule for p in problems)

    advice_lines = []
    if any_untracked:
        advice_lines.append(
            "Add these paths to .gitignore, or move the files, then run adopt again."
        )
    if any_tracked_not_ignored:
        advice_lines.append(
            "This file is tracked. Remove it from the index (git rm --cached <path>, "
            "the file stays on disk) and add the path to .gitignore (or move the file), "
            "then run adopt again."
        )
    elif any_tracked_ignored:
        advice_lines.append(
            "This file is tracked. Remove it from the index (git rm --cached <path>, "
            "the file stays on disk), then run adopt again."
        )

    message = "unsafe file(s) found:\n" + "\n".join(file_lines) + "\n" + "\n".join(advice_lines)
    data = {
        "unsafe": [
            {
                "path": p.path,
                "reason": p.reason,
                "bytes": p.size_bytes,
                "tracked": p.tracked,
            }
            for p in problems
        ]
    }
    return KechupError(2, "unsafe_file", message, data=data)


def _outer_repo_tracks_target(target: Path) -> bool:
    """Step 2: an outer git repository tracking any file inside the target."""
    parent = target.parent
    toplevel = gitutil.repo_toplevel(parent)
    if toplevel is None:
        return False
    if toplevel == target.resolve():
        return False
    try:
        target.resolve().relative_to(toplevel)
    except ValueError:
        return False
    import subprocess

    rel = target.resolve().relative_to(toplevel)
    result = subprocess.run(
        ["git", "ls-files", "--", str(rel)],
        cwd=str(toplevel),
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _is_recovery_candidate(target: Path, requires_keche_md: bool) -> bool:
    """Step 0: `<name>/` holds only bootstrap files, no `Kechup-Init: 1`
    commit."""
    if not target.exists() or not target.is_dir():
        return False
    allowed_names = set(BOOTSTRAP_FILE_NAMES) | BOOTSTRAP_DIR_NAMES
    if not requires_keche_md:
        allowed_names = allowed_names - {"keche.md"}
    for entry in target.iterdir():
        name = entry.name
        if name in allowed_names:
            continue
        if name.endswith(".tmp"):
            continue
        return False
    if is_kechup_managed(target):
        return False
    return True


def _write_bootstrap_files(
    target: Path, is_new: bool, name: str, goal: str, owner: str
) -> list[str]:
    """Step 6: write `.gitignore` and `.kechup/` only if absent. `new` also
    writes `keche.md`. Returns the list of project-relative paths written."""
    written: list[str] = []

    gitignore_path = target / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
        written.append(".gitignore")

    kechup_dir = target / ".kechup"
    kechup_dir.mkdir(exist_ok=True)

    tasks_path = tasks_json_path(target)
    if not tasks_path.exists():
        save_tasks(target, TasksFile(version=TASKS_SCHEMA_VERSION, next_id=1, tasks={}))
        written.append(".kechup/tasks.json")

    config_path = config_json_path(target)
    if not config_path.exists():
        save_config(
            target,
            {
                "version": CONFIG_SCHEMA_VERSION,
                "owner_id": owner,
                "created_at": now_iso(),
                "created_by": owner,
            },
        )
        written.append(".kechup/config.json")

    if is_new:
        keche_md_path = target / "keche.md"
        if not keche_md_path.exists():
            keche_md_path.write_text(render_template(name, goal), encoding="utf-8")
            written.append("keche.md")

    return written


@contextmanager
def _maybe_project_lock(target: Path) -> Iterator[None]:
    """R8 (SPEC sections 5/6): once `.kechup/` already exists on disk, hold
    the project lock through the final write and commit too, not only the
    bootstrap lock."""
    if (target / ".kechup").exists():
        with project_lock(target):
            yield
    else:
        yield


def _finish_bootstrap_commit(target: Path, caller: str, message: str) -> None:
    """Step 8: stage the written files and commit with `Kechup-Init: 1`."""
    paths = []
    for candidate in (".gitignore", ".kechup/tasks.json", ".kechup/config.json", "keche.md"):
        if (target / candidate).exists():
            paths.append(candidate)
    gitutil.git_add(target, paths)
    full_message = f"{message}\n\nTask: none\nKechup-Init: 1"
    gitutil.commit(target, caller, full_message, paths=paths, only=True)


def new_project(
    name: str, goal: str, owner: str | None, caller: str
) -> dict[str, Any]:
    target = Path(name).resolve()
    owner_id = owner or DEFAULT_OWNER_ID

    if target.exists() and not _is_recovery_candidate(target, requires_keche_md=True):
        raise KechupError(1, "exists", f"{name} already exists")

    recovering = target.exists() and _is_recovery_candidate(target, requires_keche_md=True)

    with bootstrap_lock(target):
        if recovering:
            return _bootstrap_crash_recovery(target, caller)

        problems = _scan_for_unsafe_files(target) if target.exists() else []
        if problems:
            raise _unsafe_file_error(problems)

        target.mkdir(parents=True, exist_ok=True)

        if _outer_repo_tracks_target(target):
            raise KechupError(
                1, "outer_repo", "an outer git repository tracks this folder"
            )

        problems = _scan_for_unsafe_files(target)
        if problems:
            raise _unsafe_file_error(problems)
        gitutil.check_hook_installable(target)

        is_fresh_git = not (target / ".git").exists()
        with _maybe_project_lock(target):
            written = _write_bootstrap_files(target, True, name, goal, owner_id)
            if is_fresh_git:
                gitutil.git_init(target)
            gitutil.install_hook(target)
            if written or is_fresh_git:
                _finish_bootstrap_commit(target, caller, "Initial project")
        return {"path": str(target), "created": True}


def adopt_project(
    folder: str, dry_run: bool, owner: str | None, caller: str
) -> dict[str, Any]:
    target = Path(folder).resolve()
    owner_id = owner or DEFAULT_OWNER_ID

    keche_md_exists = (target / "keche.md").exists()
    if not keche_md_exists:
        raise KechupError(1, "no_keche_md", "no keche.md")

    if _outer_repo_tracks_target(target):
        raise KechupError(1, "outer_repo", "an outer git repository tracks this folder")

    recovering = _is_recovery_candidate(target, requires_keche_md=False)

    if dry_run:
        problems = _scan_for_unsafe_files(target)
        if problems:
            raise _unsafe_file_error(problems)
        is_fresh_git = not (target / ".git").exists()
        would_write = []
        if not (target / ".gitignore").exists():
            would_write.append(".gitignore")
        if not tasks_json_path(target).exists():
            would_write.append(".kechup/tasks.json")
        if not config_json_path(target).exists():
            would_write.append(".kechup/config.json")
        return {
            "path": str(target),
            "dry_run": True,
            "would_init_git": is_fresh_git,
            "would_write": would_write,
            "would_commit": bool(would_write) or is_fresh_git,
        }

    with bootstrap_lock(target):
        if recovering:
            return _bootstrap_crash_recovery(target, caller)

        if is_kechup_managed(target):
            with project_lock(target):
                return {"path": str(target), "already_adopted": True}

        problems = _scan_for_unsafe_files(target)
        if problems:
            raise _unsafe_file_error(problems)
        gitutil.check_hook_installable(target)

        is_fresh_git = not (target / ".git").exists()
        with _maybe_project_lock(target):
            written = _write_bootstrap_files(target, False, "", "", owner_id)
            if is_fresh_git:
                gitutil.git_init(target)
            gitutil.install_hook(target)

            if written or is_fresh_git:
                _finish_bootstrap_commit(target, caller, "Adopt project")
                return {"path": str(target), "already_adopted": False}
        return {"path": str(target), "already_adopted": True}


def _bootstrap_crash_recovery(target: Path, caller: str) -> dict[str, Any]:
    """Re-run steps 2-3, then commit/repair (spec section 5)."""
    if _outer_repo_tracks_target(target):
        raise KechupError(1, "outer_repo", "an outer git repository tracks this folder")
    problems = _scan_for_unsafe_files(target)
    if problems:
        raise _unsafe_file_error(problems)

    try:
        if tasks_json_path(target).exists():
            load_tasks(target)
        if config_json_path(target).exists():
            load_config(target)
    except KechupError as exc:
        raise KechupError(3, exc.code, f"invalid file during recovery: {exc.message}") from exc

    is_fresh_git = not (target / ".git").exists()
    if is_fresh_git:
        gitutil.git_init(target)

    name = target.name
    goal = "(recovered)"
    written = _write_bootstrap_files(target, True, name, "(recovered project)" if goal else goal, DEFAULT_OWNER_ID)
    gitutil.install_hook(target)
    _finish_bootstrap_commit(target, caller, "Initial project")
    return {"path": str(target), "recovered": True, "written": written}


def repair(project_root: Path, caller: str) -> dict[str, Any]:
    """`kechup repair` (spec section 5): exempt from the dirty-metadata
    guard."""
    with project_lock(project_root):
        try:
            load_tasks(project_root)
            config = load_config(project_root)
        except KechupError as exc:
            raise KechupError(3, exc.code, exc.message) from exc

        run_revert_logging_prelude(project_root)

        hook_rewritten = False
        if not gitutil.hook_present_and_current(project_root):
            gitutil.install_hook(project_root)
            hook_rewritten = True

        tasks_file = load_tasks(project_root)
        rewrite_keche_md(project_root, tasks_file)

        repair_paths = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]
        committed = False
        if gitutil.working_tree_dirty(project_root, repair_paths):
            gitutil.git_add(project_root, repair_paths)
            gitutil.commit(
                project_root,
                caller,
                "Repair\n\nTask: none",
                paths=repair_paths,
                only=True,
            )
            committed = True
        return {
            "hook_rewritten": hook_rewritten,
            "owner_id": config["owner_id"],
            "committed": committed,
        }
