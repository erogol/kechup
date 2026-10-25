"""git operations: the shared commit helper, hook install, revert detection,
log, tags, foreign-repo / managed checks (spec section 7)."""

from __future__ import annotations

import stat
import subprocess
import time
from pathlib import Path

from kechup.errors import KechupError
from kechup.model import COMMIT_ID_RE

INDEX_LOCK_RETRY_DELAYS_MS = (100, 200, 400, 800, 1600)

HOOK_MARKER = "# kechup prepare-commit-msg hook v1"

HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
# Installed by kechup (spec section 7).
# If a revert is in progress and the draft message has no standard
# "This reverts commit" line, append a Kechup-Reverts trailer with the
# target commit id read from .git/REVERT_HEAD.
MSG_FILE="$1"
GIT_DIR=$(git rev-parse --git-dir)
if [ -f "$GIT_DIR/REVERT_HEAD" ]; then
    if ! grep -q "This reverts commit" "$MSG_FILE"; then
        TARGET=$(cat "$GIT_DIR/REVERT_HEAD")
        printf '\\nKechup-Reverts: %s\\n' "$TARGET" >> "$MSG_FILE"
    fi
fi
"""


def _run(
    args: list[str], cwd: Path, env: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    full_env = _git_env(env)
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise KechupError(
            3, "git_failure", f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    if extra:
        env.update(extra)
    return env


def is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def repo_toplevel(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def git_init(path: Path) -> None:
    _run(["init", "--initial-branch=main"], cwd=path)


def install_hook(project_root: Path) -> None:
    """Install .git/hooks/prepare-commit-msg (section 7)."""
    hooks_dir = project_root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "prepare-commit-msg"
    check_hook_installable(project_root)
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def check_hook_installable(project_root: Path) -> None:
    """Refuse when the project has a prepare-commit-msg hook that kechup did
    not write, or a symlink at that path. kechup never replaces a user hook."""
    hook_path = project_root / ".git" / "hooks" / "prepare-commit-msg"
    message = (
        f"{hook_path} is not a kechup hook; move it away or merge it by hand, "
        "then run the command again"
    )
    if hook_path.is_symlink():
        raise KechupError(2, "foreign_hook", message)
    if not hook_path.exists():
        return
    try:
        old = hook_path.read_bytes()
    except OSError as exc:
        raise KechupError(3, "git_error", f"cannot read {hook_path}: {exc}") from exc
    if HOOK_MARKER.encode("utf-8") not in old:
        raise KechupError(2, "foreign_hook", message)


def hook_present_and_current(project_root: Path) -> bool:
    hook_path = project_root / ".git" / "hooks" / "prepare-commit-msg"
    if not hook_path.exists():
        return False
    try:
        return hook_path.read_text(encoding="utf-8") == HOOK_SCRIPT
    except OSError:
        return False


def git_add(project_root: Path, paths: list[str]) -> None:
    if not paths:
        return
    args = ["add", "--", *paths]
    delay_iter = iter(INDEX_LOCK_RETRY_DELAYS_MS)
    while True:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            env=_git_env(),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        if "index.lock" in result.stderr and "File exists" in result.stderr:
            delay_ms = next(delay_iter, None)
            if delay_ms is None:
                raise KechupError(3, "git_failure", "git index.lock: File exists")
            time.sleep(delay_ms / 1000.0)
            continue
        raise KechupError(3, "git_failure", f"git add failed: {result.stderr.strip()}")


def commit(
    project_root: Path,
    agent_id: str,
    message: str,
    paths: list[str] | None,
    allow_empty: bool = False,
    only: bool = False,
) -> str:
    """The shared commit helper (spec section 7). Returns the new commit id.
    R10: no whole-index mode. `paths` must be explicit and non-empty,
    except the empty revert-log marker commit (`only=True,
    allow_empty=True`, no paths)."""
    if not paths and not (only and allow_empty):
        raise KechupError(
            3,
            "git_failure",
            "commit requires explicit paths (no whole-index commit mode)",
        )
    env = {
        "GIT_AUTHOR_NAME": agent_id,
        "GIT_AUTHOR_EMAIL": f"{agent_id}@kechup.local",
        "GIT_COMMITTER_NAME": agent_id,
        "GIT_COMMITTER_EMAIL": f"{agent_id}@kechup.local",
    }
    args = ["commit", "--author", f"{agent_id} <{agent_id}@kechup.local>", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    if only:
        args.append("--only")
    if paths:
        args.extend(["--", *paths])
    elif only:
        args.extend(["--"])

    delay_iter = iter(INDEX_LOCK_RETRY_DELAYS_MS)
    while True:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            env=_git_env(env),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
        if "index.lock" in result.stderr and "File exists" in result.stderr:
            delay_ms = next(delay_iter, None)
            if delay_ms is None:
                raise KechupError(3, "git_failure", "git index.lock: File exists")
            time.sleep(delay_ms / 1000.0)
            continue
        raise KechupError(3, "git_failure", f"git commit failed: {result.stderr.strip()}")
    return head_commit(project_root)


def head_commit(project_root: Path) -> str:
    result = _run(["rev-parse", "HEAD"], cwd=project_root)
    return result.stdout.strip()


def is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def commit_exists(project_root: Path, commit_id: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", commit_id + "^{commit}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def short_id(project_root: Path, commit_id: str) -> str:
    result = _run(["rev-parse", "--short", commit_id], cwd=project_root)
    return result.stdout.strip()


def commit_author_name(project_root: Path, commit_id: str) -> str:
    result = _run(["show", "-s", "--format=%an", commit_id], cwd=project_root)
    return result.stdout.strip()


def commit_author_date(project_root: Path, commit_id: str) -> str:
    """The author date, ISO 8601 (`%aI`, same format as log_entries_for_task,
    FIX_SPEC F2)."""
    result = _run(["show", "-s", "--format=%aI", commit_id], cwd=project_root)
    return result.stdout.strip()


def commit_body(project_root: Path, commit_id: str) -> str:
    result = _run(["show", "-s", "--format=%B", commit_id], cwd=project_root)
    return result.stdout


def commit_trailers(project_root: Path, commit_id: str, key: str) -> list[str]:
    result = _run(
        ["log", "-1", f"--format=%(trailers:key={key},valueonly)", commit_id],
        cwd=project_root,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def commits_reachable_from_head(project_root: Path) -> list[str]:
    result = _run(["rev-list", "HEAD"], cwd=project_root)
    return [line for line in result.stdout.splitlines() if line.strip()]


def commits_with_task_line(project_root: Path, task_id: str) -> list[str]:
    """Every commit reachable from HEAD carrying `Task: T<n>`, oldest first."""
    result = _run(
        ["log", "--reverse", f"--grep=^Task: {task_id}$", "--format=%H"],
        cwd=project_root,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def commits_matching_subject(project_root: Path, pattern: str) -> list[str]:
    result = _run(
        ["log", "--reverse", f"--grep={pattern}", "--format=%H"],
        cwd=project_root,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


REVERTS_LINE_RE = __import__("re").compile(r"This reverts commit ([0-9a-f]{40})\.")


def revert_target(project_root: Path, commit_id: str) -> str | None:
    """If `commit_id` is a revert commit (standard line or trailer), the
    target commit id it reverts. Else None."""
    body = commit_body(project_root, commit_id)
    match = REVERTS_LINE_RE.search(body)
    if match:
        return match.group(1)
    trailers = commit_trailers(project_root, commit_id, "Kechup-Reverts")
    if trailers:
        target = trailers[0].strip()
        # Only a full commit id: the value goes to git as an argument.
        if COMMIT_ID_RE.match(target):
            return target
    return None


def has_revert_seen_trailer(project_root: Path, commit_id: str, target_id: str) -> bool:
    trailers = commit_trailers(project_root, commit_id, "Kechup-Revert-Seen")
    return target_id in [t.strip() for t in trailers]


def all_revert_commits(project_root: Path) -> list[str]:
    """Every commit reachable from HEAD that is a revert commit (has a
    revert target), oldest first."""
    all_commits = list(reversed(commits_reachable_from_head(project_root)))
    return [c for c in all_commits if revert_target(project_root, c) is not None]


def working_tree_dirty(project_root: Path, paths: list[str]) -> bool:
    result = _run(["status", "--porcelain", "--", *paths], cwd=project_root)
    return bool(result.stdout.strip())


def file_matches_head(project_root: Path, rel_path: str) -> bool:
    """Whether the working-tree file is byte-identical to the HEAD copy."""
    full_path = project_root / rel_path
    if not full_path.exists():
        return False
    result = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", rel_path],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    # exit 0: no diff. exit 1: differs. Any other: error (e.g. not in HEAD).
    if result.returncode not in (0, 1):
        return False
    return result.returncode == 0


def file_in_head(project_root: Path, rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{rel_path}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def create_tag(project_root: Path, tag_name: str) -> None:
    result = subprocess.run(
        ["git", "tag", tag_name],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KechupError(3, "git_failure", f"git tag failed: {result.stderr.strip()}")


def tag_exists(project_root: Path, tag_name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag_name}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def log_entries_for_task(project_root: Path, task_id: str) -> list[dict[str, str]]:
    """`git log --grep "Task: T<n>"`, oldest first: hash, author, date, subject."""
    result = _run(
        [
            "log",
            "--reverse",
            f"--grep=^Task: {task_id}$",
            "--format=%H%x1f%an%x1f%aI%x1f%s",
        ],
        cwd=project_root,
    )
    entries = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        commit_id, author, date, subject = line.split("\x1f", 3)
        entries.append(
            {"hash": commit_id, "author": author, "date": date, "subject": subject}
        )
    return entries


def git_dir(project_root: Path) -> Path:
    """The repository's git dir, resolved to an absolute path."""
    result = _run(["rev-parse", "--absolute-git-dir"], cwd=project_root)
    return Path(result.stdout.strip())


def dirty_paths(project_root: Path, paths: list[str]) -> list[str]:
    """The subset of `paths` with changes in the worktree or index,
    NUL-delimited so names with spaces or non-ASCII pass unquoted."""
    result = _run(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
        cwd=project_root,
    )
    dirty: list[str] = []
    fields = result.stdout.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            i += 1
        if path not in dirty:
            dirty.append(path)
    return dirty


def index_entry(project_root: Path, rel_path: str) -> tuple[str, str] | None:
    """The staged entry for `rel_path`: (mode, blob sha). None when the
    path is not staged. A merge conflict (more than one stage) returns
    ("CONFLICT", ""), so callers refuse it."""
    result = _run(["ls-files", "--stage", "--", rel_path], cwd=project_root)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) > 1:
        return ("CONFLICT", "")
    meta = lines[0].split("\t", 1)[0].split()
    # <mode> <sha> <stage>\t<path>. Stage 0 is the normal entry. A
    # single nonzero-stage entry (an add/delete conflict) is a
    # conflict: callers must refuse it.
    if len(meta) != 3 or meta[2] != "0":
        return ("CONFLICT", "")
    return (meta[0], meta[1])


def blob_content_bytes(project_root: Path, blob_sha: str) -> bytes | None:
    """The raw content of a git object, or None when it does not exist."""
    result = subprocess.run(
        ["git", "cat-file", "-e", blob_sha + "^{blob}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    result = subprocess.run(
        ["git", "cat-file", "blob", blob_sha],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def head_blob_content_bytes(project_root: Path, rel_path: str) -> bytes | None:
    """The blob content of `rel_path` at HEAD, or None when the path is
    not in HEAD."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{rel_path}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    result = subprocess.run(
        ["git", "cat-file", "blob", f"HEAD:{rel_path}"],
        cwd=str(project_root),
        env=_git_env(),
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout
