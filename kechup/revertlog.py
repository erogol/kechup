"""Revert logging prelude (spec section 5). Runs right after a write
command takes the project lock, before its own body."""

from __future__ import annotations

import re
from pathlib import Path

from kechup import gitutil, journal
from kechup.model import load_tasks
from kechup.worksection import build_keche_md_text, write_keche_md

TASK_LINE_RE = re.compile(r"^Task: (T\d+)$", re.MULTILINE)
REVERT_LOG_AUTHOR = "kechup-revert-log"


def _task_line_for_commit(project_root: Path, commit_id: str) -> str | None:
    body = gitutil.commit_body(project_root, commit_id)
    match = TASK_LINE_RE.search(body)
    if match:
        return match.group(1)
    return None


def _resolve_origin_and_count(project_root: Path, revert_commit_id: str) -> tuple[str, int]:
    """Walk a revert commit's chain back to the origin (a commit with no
    revert target), counting edges up to and including the revert commit."""
    count = 1
    current = revert_commit_id
    while True:
        target = gitutil.revert_target(project_root, current)
        assert target is not None
        if gitutil.revert_target(project_root, target) is None:
            return target, count
        count += 1
        current = target


def run_revert_logging_prelude(project_root: Path) -> list[str]:
    """Scans commits reachable from HEAD for an unlogged revert commit,
    oldest first, and logs each one. Returns the messages logged (for
    tests/diagnostics)."""
    messages: list[str] = []
    while True:
        reachable_oldest_first = list(
            reversed(gitutil.commits_reachable_from_head(project_root))
        )
        seen_values = set()
        for commit_id in reachable_oldest_first:
            seen_values.update(
                v.strip() for v in gitutil.commit_trailers(
                    project_root, commit_id, "Kechup-Revert-Seen"
                )
            )
        unseen = [
            c
            for c in reachable_oldest_first
            if gitutil.revert_target(project_root, c) is not None and c not in seen_values
        ]
        if not unseen:
            break
        revert_commit = unseen[0]
        origin, count = _resolve_origin_and_count(project_root, revert_commit)
        is_odd = count % 2 == 1
        word = "reverted" if is_odd else "reinstated"
        origin_short = gitutil.short_id(project_root, origin)
        revert_short = gitutil.short_id(project_root, revert_commit)
        author = gitutil.commit_author_name(project_root, revert_commit)
        task_id = _task_line_for_commit(project_root, origin)
        task_trailer = f"Task: {task_id}" if task_id else "Task: none"

        subject = f"Work commit {origin_short} {word} by {revert_short} (author {author})"
        message = f"{subject}\n\n{task_trailer}\nKechup-Revert-Seen: {revert_commit}"

        changed = False
        if task_id:
            tasks_file = load_tasks(project_root)
            if task_id in tasks_file.tasks:
                keche_md_path = project_root / "keche.md"
                before = keche_md_path.read_text(encoding="utf-8") if keche_md_path.exists() else ""
                after = build_keche_md_text(project_root, tasks_file)
                changed = before != after

        if changed:
            journal.record(project_root, REVERT_LOG_AUTHOR, message, {"keche.md": after})
            write_keche_md(project_root, after)
            gitutil.git_add(project_root, ["keche.md"])
            gitutil.commit(
                project_root, REVERT_LOG_AUTHOR, message, paths=["keche.md"], only=True
            )
            journal.clear(project_root)
        else:
            gitutil.commit(
                project_root,
                REVERT_LOG_AUTHOR,
                message,
                paths=None,
                allow_empty=True,
                only=True,
            )
        messages.append(subject)
    return messages
