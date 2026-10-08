"""Renders and rewrites the keche.md Work section (spec section 3)."""

from __future__ import annotations

from pathlib import Path

from kechup import state as state_mod
from kechup.model import TasksFile

START_MARKER = "## Work (kechup — do not edit)"
END_MARKER = "<!-- kechup:end -->"


def render_work_section(project_root: Path, tasks_file: TasksFile) -> str:
    """Builds the text between the markers: Doing, Blocked, Todo, Done
    (last 5 by done_at, newest first)."""
    doing_lines: list[str] = []
    blocked_lines: list[str] = []
    todo_lines: list[str] = []
    done_entries: list[tuple[str, str]] = []

    for task_id in sorted(
        tasks_file.tasks.keys(), key=lambda t: int(t[1:]) if t[1:].isdigit() else 0
    ):
        task = tasks_file.tasks[task_id]
        if task.state == "dropped":
            continue
        displayed = state_mod.compute_displayed_state(project_root, tasks_file, task)
        if displayed.state == "done":
            done_entries.append((task.done_at or "", f"- {task.id}: {task.title}"))
        elif displayed.state.startswith("done —"):
            done_entries.append(
                (
                    task.done_at or "",
                    f"- {task.id}: {task.title} ({displayed.cause})",
                )
            )
        elif displayed.state == "blocked":
            blocked_lines.append(f"- {task.id}: {task.title} ({displayed.cause})")
        elif displayed.state in ("doing", "doing (stale)"):
            suffix = " (stale)" if displayed.state == "doing (stale)" else ""
            owner = task.owner or "unassigned"
            doing_lines.append(f"- {task.id}: {task.title} [{owner}]{suffix}")
        elif displayed.state == "todo":
            todo_lines.append(f"- {task.id}: {task.title}")

    done_entries.sort(key=lambda pair: pair[0], reverse=True)
    done_lines = [text for _, text in done_entries[:5]]

    lines = [START_MARKER, ""]
    lines.append("Doing:")
    lines.extend(doing_lines if doing_lines else ["- (none)"])
    lines.append("")
    lines.append("Blocked:")
    lines.extend(blocked_lines if blocked_lines else ["- (none)"])
    lines.append("")
    lines.append("Todo:")
    lines.extend(todo_lines if todo_lines else ["- (none)"])
    lines.append("")
    lines.append("Done (last 5):")
    lines.extend(done_lines if done_lines else ["- (none)"])
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def rewrite_keche_md(project_root: Path, tasks_file: TasksFile) -> None:
    """Rewrites only the text between the two marker lines in keche.md."""
    keche_md_path = project_root / "keche.md"
    section = render_work_section(project_root, tasks_file)
    if not keche_md_path.exists():
        keche_md_path.write_text(section, encoding="utf-8")
        return
    text = keche_md_path.read_text(encoding="utf-8")
    start_idx = text.find(START_MARKER)
    end_idx = text.find(END_MARKER)
    if start_idx == -1 or end_idx == -1:
        new_text = text.rstrip("\n") + "\n\n" + section
    else:
        end_idx_full = end_idx + len(END_MARKER)
        new_text = text[:start_idx] + section.rstrip("\n") + text[end_idx_full:]
    keche_md_path.write_text(new_text, encoding="utf-8")
