"""The keche.md template written by `kechup new` (spec section 3)."""

from __future__ import annotations

from kechup.worksection import END_MARKER, START_MARKER


def render_template(name: str, goal: str) -> str:
    return (
        f"# {name}\n\n"
        f"## Goal\n\n{goal}\n\n"
        f"## Rules\n\n"
        f"- Claim a task before you start it. Use `kechup claim T<n> --by <your agent id>`.\n"
        f"- Write a note with `kechup note T<n> \"<text>\"` to report progress. Any agent can add a note. The note records its author.\n"
        f"- Write `REPORT.md` in the task folder before you ask for `done`.\n\n"
        f"{START_MARKER}\n\n"
        f"Doing:\n- (none)\n\n"
        f"Blocked:\n- (none)\n\n"
        f"Todo:\n- (none)\n\n"
        f"Done (last 5):\n- (none)\n\n"
        f"{END_MARKER}\n"
    )
