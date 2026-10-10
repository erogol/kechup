---
name: kechup
description: Project task tracker. Use in any folder with .kechup/.
---

# kechup

kechup is the task tracker of one project folder. Tasks live in `.kechup/tasks.json` and in git. Run it with your shell tool inside the project folder.

## When to Use

- Any work in a folder that contains `.kechup/`.
- The owner asks you to make or adopt a project.

## Agent ID

Pass `--by <id>` on every write command. If your prompt gives you an agent ID, use it. Otherwise pick one stable ID for the whole session, for example `agent-1` or `claude:session:42`. Do not change the ID during the session.

## Procedure

1. Run `kechup list`. Run `kechup show T<n>` for the task folder and notes.
2. Claim: `kechup claim T<n> --by <id>`, or `kechup next --claim --by <id>`.
   No task fits? `kechup add task "<title>" --by <id>`, then claim it.
3. Record progress: `kechup note T<n> "<text>" --by <id>`.
4. Write the result to `<task folder>/REPORT.md`.
5. Commit: `kechup commit <paths> -m "<text>" --task T<n> --by <id>`.
6. Tell the owner. Only the owner accepts work.
7. After the owner says "I accept": `kechup done T<n> --evidence <task folder>/REPORT.md --by <owner_id>`.
   `owner_id` is in `.kechup/config.json`.

## Quick Reference

| Command | Use |
|---|---|
| `kechup unclaim T<n> --by <id>` | Give a task back. |
| `kechup block T<n> --reason "<text>" --by <id>` | Mark a task blocked. |
| `kechup reopen T<n> --reason "<text>" --by <id>` | Open a done task again. |
| `kechup log T<n>` | Git history of a task. |
| `kechup adopt <folder> --dry-run` | Preview a new project. |
| `kechup <command> --help` | Flags of a command. |

Add `--json` to any command for parseable output.

## Pitfalls

- Never run `git add -A` or a plain `git commit`. Use `kechup commit`.
- Never edit the "Work (kechup — do not edit)" section of `keche.md`.
- `note` works only on a task that you claimed.
- Exit 2 `already_claimed`: another agent has the task. Take a different task.
- Exit 3 `no_agent_id`: add `--by <id>`.
- Undo a commit: `git -c user.name=<id> -c user.email=<id>@kechup.local revert --no-edit <commit>`.
- Run the real `adopt` only after the owner approves the dry run.

## Verification

Run `kechup show T<n>` and check the claim, your notes, and the commit.
