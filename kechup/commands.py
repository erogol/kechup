"""Command implementations (spec section 5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kechup import gitutil, state as state_mod
from kechup.errors import KechupError
from kechup.guard import check_no_crash_residue
from kechup.lock import project_lock
from kechup.model import (
    Task,
    TasksFile,
    config_json_path,
    load_config,
    load_tasks,
    next_task_id,
    save_config,
    save_tasks,
    slugify,
    tasks_json_path,
    validate_title,
)
from kechup.revertlog import run_revert_logging_prelude
from kechup.safety import (
    MAX_FILE_BYTES,
    is_in_nested_repo,
    is_secret_like,
    is_too_large,
    resolve_within,
)
from kechup.timeutil import now_iso
from kechup.worksection import rewrite_keche_md

NON_TERMINAL_STATES = ("todo", "doing")


def _task_or_raise(tasks_file: TasksFile, task_id: str) -> Task:
    task = tasks_file.tasks.get(task_id)
    if task is None:
        raise KechupError(1, "unknown_task", f"unknown task id: {task_id}")
    return task


def _would_create_cycle(
    tasks_file: TasksFile, task_id: str, new_after: list[str]
) -> str | None:
    """Returns a description of the cycle, or None if there is none."""
    graph: dict[str, list[str]] = {tid: list(t.after) for tid, t in tasks_file.tasks.items()}
    graph[task_id] = list(new_after)

    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> str | None:
        if node in visiting:
            cycle_start = path.index(node)
            return " -> ".join(path[cycle_start:] + [node])
        if node in visited:
            return None
        visiting.add(node)
        path.append(node)
        for dep in graph.get(node, []):
            result = visit(dep)
            if result is not None:
                return result
        path.pop()
        visiting.discard(node)
        visited.add(node)
        return None

    return visit(task_id)


def _write_through(
    project_root: Path,
    agent_id: str,
    tasks_file: TasksFile,
    message: str,
) -> None:
    save_tasks(project_root, tasks_file)
    rewrite_keche_md(project_root, tasks_file)
    paths = [".kechup/tasks.json", "keche.md"]
    gitutil.git_add(project_root, paths)
    gitutil.commit(project_root, agent_id, message, paths=paths, only=True)


def begin_write(project_root: Path) -> None:
    """Dirty-metadata guard, then the revert-logging prelude, run right
    after the project lock (section 6)."""
    check_no_crash_residue(project_root)
    run_revert_logging_prelude(project_root)


def add_task(
    project_root: Path,
    agent_id: str,
    title: str,
    after: list[str],
    priority: int,
    folder: str | None,
) -> dict[str, Any]:
    validate_title(title)
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        for dep_id in after:
            if dep_id not in tasks_file.tasks:
                raise KechupError(1, "unknown_task", f"unknown task id: {dep_id}")
        task_id = next_task_id(tasks_file)
        cycle = _would_create_cycle(tasks_file, task_id, after)
        if cycle:
            raise KechupError(1, "cycle", f"would create a cycle: {cycle}")

        if folder:
            resolved = resolve_within(project_root, Path(folder))
            if resolved is None:
                raise KechupError(1, "bad_folder", f"--folder escapes the project: {folder}")
            rel_folder = str(resolved.relative_to(project_root.resolve()))
        else:
            slug = slugify(title)
            rel_folder = f"tasks/{task_id}-{slug}"
            (project_root / rel_folder).mkdir(parents=True, exist_ok=True)

        task = Task(
            id=task_id,
            title=title,
            state="todo",
            priority=priority,
            after=list(after),
            folder=rel_folder,
            created_at=now_iso(),
            created_by=agent_id,
        )
        tasks_file.tasks[task_id] = task
        tasks_file.next_id += 1
        _write_through(project_root, agent_id, tasks_file, f"Add {task_id}\n\nTask: {task_id}")
        return {"id": task_id, "folder": rel_folder}


def list_tasks(project_root: Path, state_filter: str | None) -> dict[str, Any]:
    tasks_file = load_tasks(project_root)
    rows = []
    for task_id in tasks_file.ordered_ids():
        task = tasks_file.tasks[task_id]
        displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
        base_state = displayed.state.split(" —")[0].split(" (")[0]
        if state_filter:
            if state_filter == "open":
                if base_state not in ("todo", "doing", "blocked"):
                    continue
            elif base_state != state_filter:
                continue
        rows.append(_task_row(project_root, tasks_file, task, displayed))
    return {"tasks": rows}


def _task_row(
    project_root: Path,
    tasks_file: TasksFile,
    task: Task,
    displayed: state_mod.DisplayedState,
) -> dict[str, Any]:
    flag = None
    if task.state == "done":
        flag_obj = state_mod.compute_done_flag(project_root, tasks_file, task)
        flag = flag_obj.text
    return {
        "id": task.id,
        "title": task.title,
        "state": displayed.state,
        "owner": task.owner,
        "claimed_at": task.claimed_at,
        "last_note_at": task.last_note_at,
        "blocker_cause": displayed.cause if displayed.state == "blocked" else None,
        "done_flag": flag,
        "folder": task.folder,
    }


def next_task(project_root: Path, agent_id: str | None, claim: bool) -> dict[str, Any]:
    if claim:
        with project_lock(project_root):
            begin_write(project_root)
            tasks_file = load_tasks(project_root)
            candidate = _pick_next(project_root, tasks_file)
            if candidate is None:
                raise KechupError(1, "no_open_task", "no task is open")
            task = tasks_file.tasks[candidate]
            task.owner = agent_id
            task.claimed_at = now_iso()
            task.state = "doing"
            _write_through(
                project_root, agent_id or "unknown", tasks_file,
                f"Claim {task.id}\n\nTask: {task.id}",
            )
            return {"id": task.id, "claimed": True}
    tasks_file = load_tasks(project_root)
    candidate = _pick_next(project_root, tasks_file)
    if candidate is None:
        raise KechupError(1, "no_open_task", "no task is open")
    return {"id": candidate, "claimed": False}


def _pick_next(project_root: Path, tasks_file: TasksFile) -> str | None:
    candidates = []
    for task_id in tasks_file.ordered_ids():
        task = tasks_file.tasks[task_id]
        displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
        if displayed.state in ("todo", "doing (stale)"):
            candidates.append(task)
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t.priority, int(t.id[1:])))
    return candidates[0].id


def claim_task(project_root: Path, agent_id: str, task_id: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
        if displayed.state not in ("todo", "doing (stale)"):
            raise KechupError(2, "already_claimed", f"already claimed by {task.owner}")
        task.owner = agent_id
        task.claimed_at = now_iso()
        task.state = "doing"
        _write_through(project_root, agent_id, tasks_file, f"Claim {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def unclaim_task(project_root: Path, agent_id: str, task_id: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state != "doing":
            raise KechupError(2, "not_doing", f"{task_id} is not doing")
        displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
        is_stale = displayed.state == "doing (stale)"
        if task.owner != agent_id and not is_stale:
            raise KechupError(2, "not_owner", f"not the owner of {task_id}")
        task.owner = None
        task.claimed_at = None
        task.state = "todo"
        _write_through(project_root, agent_id, tasks_file, f"Unclaim {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def note_task(project_root: Path, agent_id: str, task_id: str, text: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state != "doing" or task.owner != agent_id:
            raise KechupError(2, "not_owner", f"not the owner of {task_id}")
        task.notes.append({"at": now_iso(), "by": agent_id, "text": text})
        task.last_note_at = now_iso()
        task.claimed_at = now_iso()
        save_tasks(project_root, tasks_file)
        paths = [".kechup/tasks.json"]
        gitutil.git_add(project_root, paths)
        gitutil.commit(
            project_root, agent_id, f"Note {task_id}\n\nTask: {task_id}", paths=paths, only=True
        )
        return {"id": task_id}


def block_task(project_root: Path, agent_id: str, task_id: str, reason: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state in ("done", "dropped"):
            raise KechupError(2, "terminal_state", f"{task_id} is {task.state}")
        task.block_reason = reason
        _write_through(project_root, agent_id, tasks_file, f"Block {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def unblock_task(project_root: Path, agent_id: str, task_id: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state in ("done", "dropped"):
            raise KechupError(2, "terminal_state", f"{task_id} is {task.state}")
        task.block_reason = None
        _write_through(project_root, agent_id, tasks_file, f"Unblock {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def assign_task(project_root: Path, agent_id: str, task_id: str, to: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state in ("done", "dropped"):
            raise KechupError(2, "terminal_state", f"{task_id} is {task.state}")
        task.owner = to
        task.state = "doing"
        task.claimed_at = now_iso()
        _write_through(project_root, agent_id, tasks_file, f"Assign {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def done_task(project_root: Path, agent_id: str, task_id: str, evidence: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        config = load_config(project_root)
        if agent_id != config["owner_id"]:
            raise KechupError(2, "not_owner", "caller is not the configured project owner")
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state in ("done", "dropped"):
            raise KechupError(2, "terminal_state", f"{task_id} is {task.state}")
        report_rel = f"{task.folder}/REPORT.md"
        if not gitutil.file_in_head(project_root, report_rel):
            raise KechupError(2, "no_report", f"{report_rel} must exist in HEAD")
        if not gitutil.file_matches_head(project_root, report_rel):
            raise KechupError(2, "dirty_report", f"{report_rel} does not match HEAD")
        # done_commit is the HEAD id when `done` ran: the commit that
        # already held REPORT.md, read before this command's own commit.
        done_commit_id = gitutil.head_commit(project_root)
        task.state = "done"
        task.evidence = evidence
        task.done_commit = done_commit_id
        task.done_at = now_iso()
        _write_through(project_root, agent_id, tasks_file, f"Done {task_id}\n\nTask: {task_id}")
        return {"id": task_id, "done_commit": done_commit_id}


def reopen_task(project_root: Path, agent_id: str, task_id: str, reason: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state != "done":
            raise KechupError(2, "not_done", f"{task_id} is not done")
        task.done_commit = None
        task.evidence = None
        task.done_at = None
        task.state = "todo"
        task.notes.append({"at": now_iso(), "by": agent_id, "text": reason})
        task.last_note_at = now_iso()
        _write_through(
            project_root, agent_id, tasks_file, f"Reopen {task_id}\n\nTask: {task_id}"
        )
        return {"id": task_id}


def drop_task(project_root: Path, agent_id: str, task_id: str, reason: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if task.state in ("done", "dropped"):
            raise KechupError(2, "terminal_state", f"{task_id} is {task.state}")
        task.state = "dropped"
        task.drop_reason = reason
        _write_through(project_root, agent_id, tasks_file, f"Drop {task_id}\n\nTask: {task_id}")
        return {"id": task_id}


def dep_add(project_root: Path, agent_id: str, task_id: str, after_id: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        _task_or_raise(tasks_file, after_id)
        if after_id in task.after:
            return {"id": task_id, "after": list(task.after)}
        new_after = task.after + [after_id]
        cycle = _would_create_cycle(tasks_file, task_id, new_after)
        if cycle:
            raise KechupError(1, "cycle", f"would create a cycle: {cycle}")
        task.after = new_after
        _write_through(
            project_root, agent_id, tasks_file, f"Dep add {task_id} {after_id}\n\nTask: {task_id}"
        )
        return {"id": task_id, "after": list(task.after)}


def dep_rm(project_root: Path, agent_id: str, task_id: str, after_id: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        _task_or_raise(tasks_file, after_id)
        if after_id not in task.after:
            raise KechupError(
                1, "not_in_after", f"{after_id} is not in after of {task_id}"
            )
        task.after = [a for a in task.after if a != after_id]
        _write_through(
            project_root, agent_id, tasks_file,
            f"Dep rm {task_id} {after_id}\n\nTask: {task_id}",
        )
        return {"id": task_id, "after": list(task.after)}


def show(
    project_root: Path,
    task_id: str | None,
    rebuild_md: bool,
    agent_id: str | None = None,
) -> dict[str, Any]:
    if rebuild_md:
        assert agent_id is not None, "rebuild_md requires an agent id"
        with project_lock(project_root):
            begin_write(project_root)
            tasks_file = load_tasks(project_root)
            rewrite_keche_md(project_root, tasks_file)
            paths = ["keche.md"]
            gitutil.git_add(project_root, paths)
            gitutil.commit(
                project_root,
                agent_id,
                "Rebuild keche.md\n\nTask: none",
                paths=paths,
                only=True,
                allow_empty=True,
            )
    else:
        tasks_file = load_tasks(project_root)
    if task_id is None:
        counts: dict[str, int] = {}
        for tid in tasks_file.ordered_ids():
            task = tasks_file.tasks[tid]
            displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
            base = displayed.state.split(" —")[0].split(" (")[0]
            counts[base] = counts.get(base, 0) + 1
        keche_md_path = project_root / "keche.md"
        work_section = keche_md_path.read_text(encoding="utf-8") if keche_md_path.exists() else ""
        return {"counts": counts, "work_section": work_section}
    task = _task_or_raise(tasks_file, task_id)
    displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
    row = _task_row(project_root, tasks_file, task, displayed)
    row["notes"] = list(task.notes)
    row["after"] = list(task.after)
    return row


def log(project_root: Path, task_id: str) -> dict[str, Any]:
    entries = gitutil.log_entries_for_task(project_root, task_id)
    origin_ids = {e["hash"] for e in entries}
    reachable_oldest_first = list(
        reversed(gitutil.commits_reachable_from_head(project_root))
    )
    for commit_id in reachable_oldest_first:
        target = gitutil.revert_target(project_root, commit_id)
        if target is None:
            continue
        current = target
        is_in_chain = current in origin_ids
        seen_chain = {current}
        while not is_in_chain:
            next_target = gitutil.revert_target(project_root, current)
            if next_target is None or next_target in seen_chain:
                break
            current = next_target
            seen_chain.add(current)
            if current in origin_ids:
                is_in_chain = True
        if is_in_chain and commit_id not in [e["hash"] for e in entries]:
            entries.append(
                {
                    "hash": commit_id,
                    "author": gitutil.commit_author_name(project_root, commit_id),
                    "date": gitutil.commit_author_date(project_root, commit_id),
                    "subject": gitutil.commit_body(project_root, commit_id).splitlines()[0],
                }
            )
    position = {c: i for i, c in enumerate(reachable_oldest_first)}
    entries.sort(key=lambda e: position.get(e["hash"], 0))
    return {"entries": entries}


def commit_paths(
    project_root: Path, agent_id: str, paths: list[str], message: str, task_id: str | None
) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        resolved_rel: list[str] = []
        for raw_path in paths:
            candidate = Path(raw_path)
            resolved = resolve_within(project_root, candidate)
            if resolved is None:
                raise KechupError(3, "path_escape", f"path escapes the project: {raw_path}")
            if resolved.is_dir():
                rel_dir = resolved.relative_to(project_root.resolve())
                if rel_dir.parts and rel_dir.parts[0] == ".git":
                    raise KechupError(3, "path_escape", f"path is inside .git: {raw_path}")
                if is_in_nested_repo(project_root, str(rel_dir)):
                    raise KechupError(
                        3, "path_escape", f"path is inside a nested repo: {raw_path}"
                    )
                for sub in sorted(resolved.rglob("*")):
                    if not sub.is_file():
                        continue
                    sub_resolved = resolve_within(project_root, sub)
                    if sub_resolved is None:
                        raise KechupError(
                            3, "path_escape", f"a file in {raw_path} escapes the project: {sub}"
                        )
                    rel = sub_resolved.relative_to(project_root.resolve())
                    if rel.parts and rel.parts[0] == ".git":
                        continue
                    if is_in_nested_repo(project_root, str(rel)):
                        continue
                    resolved_rel.append(str(rel))
            elif resolved.exists():
                rel = resolved.relative_to(project_root.resolve())
                if rel.parts and rel.parts[0] == ".git":
                    raise KechupError(3, "path_escape", f"path is inside .git: {raw_path}")
                if is_in_nested_repo(project_root, str(rel)):
                    raise KechupError(
                        3, "path_escape", f"path is inside a nested repo: {raw_path}"
                    )
                resolved_rel.append(str(rel))
            else:
                raise KechupError(3, "not_found", f"path does not exist: {raw_path}")

        for rel in resolved_rel:
            full = project_root / rel
            if is_secret_like(Path(rel)):
                raise KechupError(3, "secret_like", f"refusing a secret-like file: {rel}")
            if is_too_large(full):
                raise KechupError(3, "too_large", f"file over 5 MB: {rel}")

        task_line = task_id if task_id else "none"
        full_message = f"{message}\n\nTask: {task_line}"
        gitutil.git_add(project_root, resolved_rel)
        commit_id = gitutil.commit(
            project_root, agent_id, full_message, paths=resolved_rel, only=True
        )
        return {"commit": commit_id, "paths": resolved_rel}


def approve(project_root: Path, agent_id: str, task_id: str, kind: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        tasks_file = load_tasks(project_root)
        task = _task_or_raise(tasks_file, task_id)
        if gitutil.working_tree_dirty(project_root, [task.folder]):
            raise KechupError(2, "dirty_tree", f"{task.folder} has uncommitted changes")
        tag_name = f"{task_id}-{kind}-approved"
        if gitutil.tag_exists(project_root, tag_name):
            raise KechupError(2, "tag_exists", f"tag already exists: {tag_name}")
        gitutil.create_tag(project_root, tag_name)
        return {"tag": tag_name}


def snapshot(project_root: Path) -> dict[str, Any]:
    """FIX_SPEC_F3 R2: `snapshot` never refuses because of one file. An
    oversized or secret-like eligible file is skipped and reported; every
    other eligible file (new files in new folders, tracked deletions,
    renames, names with spaces or non-ASCII letters) is still committed.
    `kechup commit` keeps its own exit-3 refusal (SPEC.md section 8)."""
    with project_lock(project_root):
        begin_write(project_root)
        rows = _run_status_porcelain(project_root)
        eligible: list[str] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _consider(rel_path: str, allow_missing: bool) -> None:
            if rel_path in seen:
                return
            seen.add(rel_path)
            if rel_path == ".git" or rel_path.startswith(".git/"):
                return
            if is_in_nested_repo(project_root, rel_path):
                return
            if resolve_within(project_root, Path(rel_path)) is None:
                return
            full = project_root / rel_path
            if not full.exists():
                if allow_missing:
                    eligible.append(rel_path)
                return
            if full.is_dir():
                return
            try:
                size = full.stat().st_size
            except OSError:
                return
            if is_secret_like(Path(rel_path)):
                skipped.append({"path": rel_path, "reason": "secret-like", "bytes": size})
                return
            if size > MAX_FILE_BYTES:
                skipped.append({"path": rel_path, "reason": "over 5 MB", "bytes": size})
                return
            eligible.append(rel_path)

        for status, path, old_path in rows:
            is_deletion = "D" in status
            if old_path is not None:
                _consider(old_path, allow_missing=True)
            _consider(path, allow_missing=is_deletion)

        if not eligible:
            return {"committed": False, "skipped": skipped}
        existing = [p for p in eligible if (project_root / p).exists()]
        gitutil.git_add(project_root, existing)
        gitutil.commit(
            project_root,
            "snapshot",
            "Automatic snapshot\n\nTask: none",
            paths=eligible,
            only=True,
        )
        return {"committed": True, "paths": eligible, "skipped": skipped}


def _run_status_porcelain(project_root: Path) -> list[tuple[str, str, str | None]]:
    """FIX_SPEC_F3 R2: `-z --untracked-files=all` so every file inside a
    new folder is listed (not just the folder), paths are NUL-delimited
    (names with spaces/non-ASCII pass through unquoted), and a rename
    record yields (new_path, old_path). Raises on a non-zero git exit —
    a failed scan is never treated as empty."""
    import subprocess

    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KechupError(3, "git_error", f"git status failed: {result.stderr.strip()}")
    fields = result.stdout.split("\0")
    rows: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        old_path = None
        if "R" in status or "C" in status:
            old_path = fields[i]
            i += 1
        rows.append((status, path, old_path))
    return rows


def config_set_owner(project_root: Path, agent_id: str, new_owner: str) -> dict[str, Any]:
    with project_lock(project_root):
        begin_write(project_root)
        config = load_config(project_root)
        if agent_id != config["owner_id"]:
            raise KechupError(2, "not_owner", "caller is not the current project owner")
        config["owner_id"] = new_owner
        save_config(project_root, config)
        paths = [".kechup/config.json"]
        gitutil.git_add(project_root, paths)
        gitutil.commit(
            project_root, agent_id, "Config set-owner\n\nTask: none", paths=paths, only=True
        )
        return {"owner_id": new_owner}
