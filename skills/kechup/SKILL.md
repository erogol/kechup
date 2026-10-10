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

Pass `--by <id>` on every write command. Use the `[Agent id: ...]` value from your prompt, for example `keche-dev:session:<id>` or `keche-dev:mission:<id>`. If there is no Agent id line, use `<instance>:session:<Session id>`. Never use `keche:`, `main`, or a made-up ID.

## Procedure

1. Run `kechup list`. Run `kechup show T<n>` for the task folder and notes.
2. Claim: `kechup claim T<n> --by <id>`, or `kechup next --claim --by <id>`.
   No task fits? `kechup add task "<title>" --by <id>`, then claim it.
3. Record progress: `kechup note T<n> "<text>" --by <id>`.
4. Write the result to `<task folder>/REPORT.md`.
5. Commit: `kechup commit <paths> -m "<text>" --task T<n> --by <id>`.
6. Run the tests and the review the task needs. Fix the failures.
7. When all pass, close the task yourself: `kechup done T<n> --evidence <task folder>/REPORT.md --by <id>`.
   Do not ask a human to accept the task.

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
