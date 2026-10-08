"""Done-flag and displayed-state computation (spec section 4). Computed at
read time, never stored."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kechup import gitutil
from kechup.model import Task, TasksFile
from kechup.timeutil import now_utc, parse_iso

STORED_STATES = ("todo", "doing", "done", "dropped")
STALE_CLAIM_SECONDS = 24 * 3600


@dataclass
class DoneFlag:
    text: str | None  # None means no flag


def _reopen_commits_for_task(project_root: Path, task_id: str) -> list[str]:
    """Every commit reachable from HEAD with first line `Reopen T<n>`,
    oldest first."""
    return gitutil.commits_matching_subject(project_root, f"^Reopen {task_id}$")


def _revert_chain_odd(
    origin: str, revert_commits_by_target: dict[str, list[str]]
) -> tuple[bool, str | None]:
    """Walk the revert chain starting at `origin`. Returns (is_odd, last_revert_id).
    `revert_commits_by_target` maps a target commit id to the revert commits
    (reachable from HEAD) that target it, oldest first."""
    count = 0
    last_revert: str | None = None
    current = origin
    seen: set[str] = set()
    while True:
        candidates = [
            c for c in revert_commits_by_target.get(current, []) if c not in seen
        ]
        if not candidates:
            break
        revert_commit = candidates[0]
        seen.add(revert_commit)
        count += 1
        last_revert = revert_commit
        current = revert_commit
    return (count % 2 == 1), last_revert


def _build_revert_index(
    project_root: Path, reachable_oldest_first: list[str]
) -> dict[str, list[str]]:
    """Map target commit id -> revert commits (reachable from HEAD) that
    target it, oldest first."""
    position = {c: i for i, c in enumerate(reachable_oldest_first)}
    revert_by_target: dict[str, list[str]] = {}
    for rc in reachable_oldest_first:
        target = gitutil.revert_target(project_root, rc)
        if target is None:
            continue
        revert_by_target.setdefault(target, []).append(rc)
    for target_list in revert_by_target.values():
        target_list.sort(key=lambda c: position[c])
    return revert_by_target


def compute_done_flag(
    project_root: Path, tasks_file: TasksFile, task: Task
) -> DoneFlag:
    """Spec section 4: computes the done_flag for a stored-`done` task."""
    if task.state != "done":
        return DoneFlag(None)
    done_commit = task.done_commit
    if not done_commit:
        return DoneFlag(None)
    head = gitutil.head_commit(project_root)
    if not gitutil.commit_exists(project_root, done_commit) or not gitutil.is_ancestor(
        project_root, done_commit, head
    ):
        return DoneFlag("done commit not in history")

    reopen_commits = _reopen_commits_for_task(project_root, task.id)
    reopen_ancestors = [
        c for c in reopen_commits if gitutil.is_ancestor(project_root, c, done_commit)
    ]
    reopen_boundary = reopen_ancestors[-1] if reopen_ancestors else None

    task_commits = gitutil.commits_with_task_line(project_root, task.id)
    candidate_commits = [
        c for c in task_commits if gitutil.is_ancestor(project_root, c, done_commit)
    ]
    if reopen_boundary is not None:
        candidate_commits = [
            c
            for c in candidate_commits
            if not gitutil.is_ancestor(project_root, c, reopen_boundary)
        ]

    reachable_oldest_first = list(reversed(gitutil.commits_reachable_from_head(project_root)))
    revert_by_target = _build_revert_index(project_root, reachable_oldest_first)

    for candidate in candidate_commits:
        is_odd, last_revert = _revert_chain_odd(candidate, revert_by_target)
        if is_odd and last_revert is not None:
            author = gitutil.commit_author_name(project_root, last_revert)
            short = gitutil.short_id(project_root, last_revert)
            return DoneFlag(f"WORK REVERTED by {short} ({author})")

    report_path = f"{task.folder}/REPORT.md"
    if not gitutil.file_in_head(project_root, report_path):
        return DoneFlag("REPORT.md missing")

    return DoneFlag(None)


@dataclass
class DisplayedState:
    state: str
    cause: str | None = None


def _claim_is_stale(task: Task) -> bool:
    if task.claimed_at is None:
        return False
    claimed = parse_iso(task.claimed_at)
    age = (now_utc() - claimed).total_seconds()
    return age > STALE_CLAIM_SECONDS


def compute_displayed_state(
    project_root: Path, tasks_file: TasksFile, task: Task
) -> DisplayedState:
    """Spec section 4, checked in order."""
    if task.state == "dropped":
        return DisplayedState("dropped")

    if task.state == "done":
        flag = compute_done_flag(project_root, tasks_file, task)
        if flag.text is None:
            return DisplayedState("done")
        return DisplayedState(f"done — {flag.text}", flag.text)

    if task.block_reason:
        return DisplayedState("blocked", task.block_reason)

    if task.after:
        causes = []
        for dep_id in task.after:
            dep = tasks_file.tasks.get(dep_id)
            if dep is None:
                causes.append(f"waits for {dep_id}")
                continue
            if dep.state == "dropped":
                causes.append(f"waits for dropped {dep_id}")
            elif dep.state != "done":
                causes.append(f"waits for {dep_id}")
            else:
                dep_flag = compute_done_flag(project_root, tasks_file, dep)
                if dep_flag.text is not None:
                    causes.append(f"waits for {dep_id} (work reverted)")
        if causes:
            return DisplayedState("blocked", "; ".join(causes))

    if task.state == "doing":
        if _claim_is_stale(task):
            return DisplayedState("doing (stale)")

    return DisplayedState(task.state)


def is_displayed_blocked(project_root: Path, tasks_file: TasksFile, task: Task) -> bool:
    return compute_displayed_state(project_root, tasks_file, task).state == "blocked"
