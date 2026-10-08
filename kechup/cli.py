"""Argument parsing and dispatch (spec section 5)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

from kechup import bootstrap, commands
from kechup.agentid import resolve_agent
from kechup.errors import KechupError
from kechup.jsonenv import dump, err_envelope, ok_envelope
from kechup.project import find_project_root


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kechup")
    parser.add_argument("--json", action="store_true", help="emit the --json envelope")
    sub = parser.add_subparsers(dest="command", required=True)
    # `--json` must work both before and after the subcommand name, since
    # argparse subparsers do not inherit the parent's optional arguments.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument("--json", action="store_true", dest="json_sub", default=False)

    p_new = sub.add_parser("new", parents=[json_parent])
    p_new.add_argument("name")
    p_new.add_argument("--goal", required=True)
    p_new.add_argument("--owner")
    p_new.add_argument("--by")

    p_adopt = sub.add_parser("adopt", parents=[json_parent])
    p_adopt.add_argument("folder")
    p_adopt.add_argument("--dry-run", action="store_true")
    p_adopt.add_argument("--owner")
    p_adopt.add_argument("--by")

    p_add = sub.add_parser("add", parents=[json_parent])
    add_sub = p_add.add_subparsers(dest="add_kind", required=True)
    p_add_task = add_sub.add_parser("task", parents=[json_parent])
    p_add_task.add_argument("title")
    p_add_task.add_argument("--after")
    p_add_task.add_argument("--priority", type=int, default=5)
    p_add_task.add_argument("--folder")
    p_add_task.add_argument("--by")

    p_list = sub.add_parser("list", parents=[json_parent])
    p_list.add_argument(
        "--state", choices=["todo", "doing", "blocked", "done", "dropped", "open"]
    )

    p_next = sub.add_parser("next", parents=[json_parent])
    p_next.add_argument("--claim", action="store_true")
    p_next.add_argument("--by")

    p_claim = sub.add_parser("claim", parents=[json_parent])
    p_claim.add_argument("task_id")
    p_claim.add_argument("--by")

    p_unclaim = sub.add_parser("unclaim", parents=[json_parent])
    p_unclaim.add_argument("task_id")
    p_unclaim.add_argument("--by")

    p_note = sub.add_parser("note", parents=[json_parent])
    p_note.add_argument("task_id")
    p_note.add_argument("text")
    p_note.add_argument("--by")

    p_block = sub.add_parser("block", parents=[json_parent])
    p_block.add_argument("task_id")
    p_block.add_argument("--reason", required=True)
    p_block.add_argument("--by")

    p_unblock = sub.add_parser("unblock", parents=[json_parent])
    p_unblock.add_argument("task_id")
    p_unblock.add_argument("--by")

    p_assign = sub.add_parser("assign", parents=[json_parent])
    p_assign.add_argument("task_id")
    p_assign.add_argument("--to", required=True)
    p_assign.add_argument("--by")

    p_done = sub.add_parser("done", parents=[json_parent])
    p_done.add_argument("task_id")
    p_done.add_argument("--evidence", required=True)
    p_done.add_argument("--by")

    p_reopen = sub.add_parser("reopen", parents=[json_parent])
    p_reopen.add_argument("task_id")
    p_reopen.add_argument("--reason", required=True)
    p_reopen.add_argument("--by")

    p_drop = sub.add_parser("drop", parents=[json_parent])
    p_drop.add_argument("task_id")
    p_drop.add_argument("--reason", required=True)
    p_drop.add_argument("--by")

    p_dep = sub.add_parser("dep", parents=[json_parent])
    dep_sub = p_dep.add_subparsers(dest="dep_kind", required=True)
    p_dep_add = dep_sub.add_parser("add", parents=[json_parent])
    p_dep_add.add_argument("task_id")
    p_dep_add.add_argument("--after", required=True)
    p_dep_add.add_argument("--by")
    p_dep_rm = dep_sub.add_parser("rm", parents=[json_parent])
    p_dep_rm.add_argument("task_id")
    p_dep_rm.add_argument("after_id")
    p_dep_rm.add_argument("--by")

    p_show = sub.add_parser("show", parents=[json_parent])
    p_show.add_argument("task_id", nargs="?")
    p_show.add_argument("--rebuild-md", action="store_true")
    p_show.add_argument("--by")

    p_log = sub.add_parser("log", parents=[json_parent])
    p_log.add_argument("task_id")

    p_commit = sub.add_parser("commit", parents=[json_parent])
    p_commit.add_argument("paths", nargs="+")
    p_commit.add_argument("-m", "--message", required=True)
    p_commit.add_argument("--task")
    p_commit.add_argument("--by")

    p_approve = sub.add_parser("approve", parents=[json_parent])
    p_approve.add_argument("task_id")
    p_approve.add_argument("kind")
    p_approve.add_argument("--by")

    p_snapshot = sub.add_parser("snapshot", parents=[json_parent])
    # Accepted so one agent rule ("pass --by on every write") works; the
    # snapshot author stays "snapshot" (owner decision 14).
    p_snapshot.add_argument("--by")

    p_config = sub.add_parser("config", parents=[json_parent])
    config_sub = p_config.add_subparsers(dest="config_kind", required=True)
    p_set_owner = config_sub.add_parser("set-owner", parents=[json_parent])
    p_set_owner.add_argument("owner_id")
    p_set_owner.add_argument("--by")

    p_repair = sub.add_parser("repair", parents=[json_parent])
    p_repair.add_argument("--by")

    return parser


def _needs_agent(command: str, add_kind: str | None, dep_kind: str | None) -> bool:
    read_only = {"list", "show", "log"}
    if command in read_only:
        return False
    if command == "next":
        return False  # only needed with --claim, checked separately
    if command == "snapshot":
        return False
    return True


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    cwd = Path.cwd()

    if command == "new":
        agent_id = resolve_agent(args.by)
        data = bootstrap.new_project(args.name, args.goal, args.owner, agent_id)
        return ok_envelope("new", data)

    if command == "adopt":
        agent_id = resolve_agent(args.by) if not args.dry_run else (args.by or "dry-run")
        data = bootstrap.adopt_project(args.folder, args.dry_run, args.owner, agent_id)
        return ok_envelope("adopt", data)

    if command == "repair":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = bootstrap.repair(project_root, agent_id)
        return ok_envelope("repair", data)

    if command == "add" and args.add_kind == "task":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.add_task(
            project_root,
            agent_id,
            args.title,
            _split_csv(args.after),
            args.priority,
            args.folder,
        )
        return ok_envelope("add task", data)

    if command == "list":
        project_root = find_project_root(cwd)
        data = commands.list_tasks(project_root, args.state)
        return ok_envelope("list", data)

    if command == "next":
        project_root = find_project_root(cwd)
        agent_id = resolve_agent(args.by) if args.claim else None
        data = commands.next_task(project_root, agent_id, args.claim)
        return ok_envelope("next", data)

    if command == "claim":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.claim_task(project_root, agent_id, args.task_id)
        return ok_envelope("claim", data)

    if command == "unclaim":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.unclaim_task(project_root, agent_id, args.task_id)
        return ok_envelope("unclaim", data)

    if command == "note":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.note_task(project_root, agent_id, args.task_id, args.text)
        return ok_envelope("note", data)

    if command == "block":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.block_task(project_root, agent_id, args.task_id, args.reason)
        return ok_envelope("block", data)

    if command == "unblock":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.unblock_task(project_root, agent_id, args.task_id)
        return ok_envelope("unblock", data)

    if command == "assign":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.assign_task(project_root, agent_id, args.task_id, args.to)
        return ok_envelope("assign", data)

    if command == "done":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.done_task(project_root, agent_id, args.task_id, args.evidence)
        return ok_envelope("done", data)

    if command == "reopen":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.reopen_task(project_root, agent_id, args.task_id, args.reason)
        return ok_envelope("reopen", data)

    if command == "drop":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.drop_task(project_root, agent_id, args.task_id, args.reason)
        return ok_envelope("drop", data)

    if command == "dep" and args.dep_kind == "add":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.dep_add(project_root, agent_id, args.task_id, args.after)
        return ok_envelope("dep add", data)

    if command == "dep" and args.dep_kind == "rm":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.dep_rm(project_root, agent_id, args.task_id, args.after_id)
        return ok_envelope("dep rm", data)

    if command == "show":
        project_root = find_project_root(cwd)
        if args.rebuild_md:
            agent_id = resolve_agent(args.by)
            data = commands.show(project_root, args.task_id, args.rebuild_md, agent_id)
        else:
            data = commands.show(project_root, args.task_id, args.rebuild_md)
        return ok_envelope("show", data)

    if command == "log":
        project_root = find_project_root(cwd)
        data = commands.log(project_root, args.task_id)
        return ok_envelope("log", data)

    if command == "commit":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.commit_paths(
            project_root, agent_id, args.paths, args.message, args.task
        )
        return ok_envelope("commit", data)

    if command == "approve":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.approve(project_root, agent_id, args.task_id, args.kind)
        return ok_envelope("approve", data)

    if command == "snapshot":
        project_root = find_project_root(cwd)
        data = commands.snapshot(project_root)
        return ok_envelope("snapshot", data)

    if command == "config" and args.config_kind == "set-owner":
        agent_id = resolve_agent(args.by)
        project_root = find_project_root(cwd)
        data = commands.config_set_owner(project_root, agent_id, args.owner_id)
        return ok_envelope("config set-owner", data)

    raise KechupError(1, "unknown_command", f"unknown command: {command}")


def _format_item(item: Any) -> str:
    """One line for one list item (FIX_SPEC F3): a dict prints as
    `key=value` pairs, never as a Python repr."""
    if isinstance(item, dict):
        return ", ".join(f"{k}={v}" for k, v in item.items())
    return str(item)


def _print_value(key: str, value: Any) -> None:
    """One field per line. A list or dict value prints one item per line,
    indented, never as a Python repr (FIX_SPEC F3)."""
    if isinstance(value, list):
        if not value:
            print(f"  {key}: (none)")
            return
        print(f"  {key}:")
        for item in value:
            print(f"    {_format_item(item)}")
        return
    if isinstance(value, dict):
        if not value:
            print(f"  {key}: (none)")
            return
        print(f"  {key}:")
        for sub_key, sub_value in value.items():
            print(f"    {sub_key}: {sub_value}")
        return
    print(f"  {key}: {value}")


def _print_human(envelope: dict[str, Any]) -> None:
    if envelope["ok"]:
        command = envelope["command"]
        data = envelope["data"]
        if command == "list" and "tasks" in data:
            for row in data["tasks"]:
                print(
                    f"{row['id']}\t{row['state']}\t{row['owner'] or '-'}\t{row['title']}"
                )
            return
        if command == "log" and "entries" in data:
            for entry in data["entries"]:
                short_hash = entry["hash"][:7]
                print(f"{short_hash}  {entry['date']}  {entry['author']}  {entry['subject']}")
            return
        print(f"ok: {command}")
        for key, value in data.items():
            _print_value(key, value)
    else:
        print(f"error [{envelope['error']['code']}]: {envelope['error']['message']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command_name = args.command
    want_json = bool(getattr(args, "json", False) or getattr(args, "json_sub", False))
    try:
        envelope = dispatch(args)
    except KechupError as exc:
        envelope = err_envelope(command_name, exc.code, exc.message, data=exc.data)
        if want_json:
            print(dump(envelope))
        else:
            _print_human(envelope)
        return exc.exit_code

    if want_json:
        print(dump(envelope))
    else:
        _print_human(envelope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
