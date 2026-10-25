"""tasks.json and config.json: schema, atomic read/write (spec section 3)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kechup.errors import KechupError

TASK_ID_RE = re.compile(r"^T(\d+)$")
# A full git commit id (SHA-1 or SHA-256). Values go to git as arguments,
# so a value that starts with "-" must never get through.
COMMIT_ID_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
TITLE_MAX_CHARS = 200
TASKS_SCHEMA_VERSION = 1
CONFIG_SCHEMA_VERSION = 1
DEFAULT_OWNER_ID = "owner"


@dataclass
class Task:
    id: str
    title: str
    state: str
    priority: int = 5
    owner: str | None = None
    claimed_at: str | None = None
    last_note_at: str | None = None
    after: list[str] = field(default_factory=list)
    block_reason: str | None = None
    drop_reason: str | None = None
    evidence: str | None = None
    done_at: str | None = None
    done_commit: str | None = None
    folder: str = ""
    notes: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    created_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "priority": self.priority,
            "owner": self.owner,
            "claimed_at": self.claimed_at,
            "last_note_at": self.last_note_at,
            "after": list(self.after),
            "block_reason": self.block_reason,
            "drop_reason": self.drop_reason,
            "evidence": self.evidence,
            "done_at": self.done_at,
            "done_commit": self.done_commit,
            "folder": self.folder,
            "notes": [dict(n) for n in self.notes],
            "created_at": self.created_at,
            "created_by": self.created_by,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Task":
        return Task(
            id=data["id"],
            title=data["title"],
            state=data["state"],
            priority=data.get("priority", 5),
            owner=data.get("owner"),
            claimed_at=data.get("claimed_at"),
            last_note_at=data.get("last_note_at"),
            after=list(data.get("after", [])),
            block_reason=data.get("block_reason"),
            drop_reason=data.get("drop_reason"),
            evidence=data.get("evidence"),
            done_at=data.get("done_at"),
            done_commit=data.get("done_commit"),
            folder=data.get("folder", ""),
            notes=[dict(n) for n in data.get("notes", [])],
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", ""),
        )


@dataclass
class TasksFile:
    version: int
    next_id: int
    tasks: dict[str, Task]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "next_id": self.next_id,
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TasksFile":
        tasks = {
            tid: Task.from_dict(tdata) for tid, tdata in data.get("tasks", {}).items()
        }
        return TasksFile(
            version=data["version"],
            next_id=data.get("next_id", 1),
            tasks=tasks,
        )

    def ordered_ids(self) -> list[str]:
        def _n(tid: str) -> int:
            m = TASK_ID_RE.match(tid)
            return int(m.group(1)) if m else 0

        return sorted(self.tasks.keys(), key=_n)


def tasks_json_path(project_root: Path) -> Path:
    return project_root / ".kechup" / "tasks.json"


def config_json_path(project_root: Path) -> Path:
    return project_root / ".kechup" / "config.json"


def atomic_write_text(path: Path, text: str) -> None:
    """Write atomically: write to `<path>.tmp`, fsync, os.replace (section 6)."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    os.replace(tmp_path, path)


VALID_TASK_STATES = ("todo", "doing", "done", "dropped")
TASK_REQUIRED_STR_FIELDS = ("id", "title", "state", "folder", "created_at", "created_by")
TASK_NULLABLE_STR_FIELDS = (
    "owner",
    "claimed_at",
    "last_note_at",
    "block_reason",
    "drop_reason",
    "evidence",
    "done_at",
    "done_commit",
)


def _invalid_tasks(msg: str) -> KechupError:
    return KechupError(3, "invalid_tasks", f"tasks.json is invalid: {msg}")


def _validate_tasks_schema(data: Any) -> None:
    """R7 (SPEC section 3): the first problem found names the task id and
    field, exit 3, code `invalid_tasks`."""
    if not isinstance(data, dict):
        raise _invalid_tasks("top-level value must be an object")
    if "version" not in data:
        raise _invalid_tasks("missing version")
    if "next_id" not in data:
        raise _invalid_tasks("missing next_id")
    next_id = data["next_id"]
    if isinstance(next_id, bool) or not isinstance(next_id, int):
        raise _invalid_tasks("next_id must be an integer")
    if "tasks" not in data:
        raise _invalid_tasks("missing tasks")
    tasks = data["tasks"]
    if not isinstance(tasks, dict):
        raise _invalid_tasks("tasks must be an object")
    for task_id in sorted(tasks.keys()):
        task = tasks[task_id]
        if not isinstance(task, dict):
            raise _invalid_tasks(f"task {task_id} must be an object")
        for field_name in TASK_REQUIRED_STR_FIELDS:
            if field_name not in task:
                raise _invalid_tasks(f"task {task_id} is missing {field_name}")
            if not isinstance(task[field_name], str):
                raise _invalid_tasks(f"task {task_id} field {field_name} must be a string")
        if task["id"] != task_id:
            raise _invalid_tasks(f"task {task_id} id field does not match its key")
        if task["state"] not in VALID_TASK_STATES:
            raise _invalid_tasks(f"task {task_id} has an unknown state {task['state']!r}")
        for field_name in TASK_NULLABLE_STR_FIELDS:
            value = task.get(field_name)
            if value is not None and not isinstance(value, str):
                raise _invalid_tasks(f"task {task_id} field {field_name} must be a string or null")
        done_commit = task.get("done_commit")
        if done_commit is not None and not COMMIT_ID_RE.match(done_commit):
            raise _invalid_tasks(f"task {task_id} field done_commit must be a full commit id")
        priority = task.get("priority", 5)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise _invalid_tasks(f"task {task_id} field priority must be an integer")
        after = task.get("after", [])
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
            raise _invalid_tasks(f"task {task_id} field after must be a list of strings")
        notes = task.get("notes", [])
        if not isinstance(notes, list):
            raise _invalid_tasks(f"task {task_id} field notes must be a list")
        for note in notes:
            if (
                not isinstance(note, dict)
                or not isinstance(note.get("at"), str)
                or not isinstance(note.get("by"), str)
                or not isinstance(note.get("text"), str)
            ):
                raise _invalid_tasks(f"task {task_id} has a malformed note")
    for task_id in sorted(tasks.keys()):
        for after_id in tasks[task_id].get("after", []):
            if after_id not in tasks:
                raise _invalid_tasks(f"task {task_id} depends on unknown task {after_id}")


def load_tasks(project_root: Path) -> TasksFile:
    path = tasks_json_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise KechupError(3, "bad_tasks_json", f"tasks.json is invalid: {exc}") from exc
    _validate_tasks_schema(data)
    return TasksFile.from_dict(data)


def serialize_tasks(tasks_file: TasksFile) -> str:
    """The exact bytes `save_tasks` writes (CRASH_RECOVERY_SPEC v2)."""
    return json.dumps(tasks_file.to_dict(), indent=2, sort_keys=True) + "\n"


def save_tasks(project_root: Path, tasks_file: TasksFile) -> None:
    path = tasks_json_path(project_root)
    atomic_write_text(path, serialize_tasks(tasks_file))


CONFIG_REQUIRED_STR_FIELDS = ("owner_id", "created_at", "created_by")


def _invalid_config(msg: str) -> KechupError:
    return KechupError(3, "invalid_config", f"config.json is invalid: {msg}")


def _validate_config_schema(data: Any) -> None:
    """R7 (SPEC section 3): the first problem found names the field."""
    if not isinstance(data, dict):
        raise _invalid_config("top-level value must be an object")
    if "version" not in data:
        raise _invalid_config("missing version")
    for field_name in CONFIG_REQUIRED_STR_FIELDS:
        if field_name not in data:
            raise _invalid_config(f"missing {field_name}")
        if not isinstance(data[field_name], str):
            raise _invalid_config(f"field {field_name} must be a string")


def load_config(project_root: Path) -> dict[str, Any]:
    path = config_json_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise KechupError(3, "bad_config_json", f"config.json is invalid: {exc}") from exc
    _validate_config_schema(data)
    return data


def serialize_config(config: dict[str, Any]) -> str:
    """The exact bytes `save_config` writes (CRASH_RECOVERY_SPEC v2)."""
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


def save_config(project_root: Path, config: dict[str, Any]) -> None:
    path = config_json_path(project_root)
    atomic_write_text(path, serialize_config(config))


def validate_title(title: str) -> None:
    if not (1 <= len(title) <= TITLE_MAX_CHARS):
        raise KechupError(
            1, "bad_title", f"title must be 1-{TITLE_MAX_CHARS} chars"
        )


def next_task_id(tasks_file: TasksFile) -> str:
    return f"T{tasks_file.next_id}"


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:60] or "task"
