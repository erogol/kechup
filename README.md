# kechup

[![Made with KeCHe](https://img.shields.io/badge/made_with-KeCHe-8A2BE2)](https://github.com/coqui-ai/keche)

kechup is a task tracker for one project folder. It is made for AI agents, and people can use it too.

Each project keeps its tasks in a small JSON file and in git. Any agent can see the open tasks, claim one, record notes, commit its work, and close the task when its tests and review pass.

kechup uses the Python standard library and the `git` command. It has no other dependencies.

## Why kechup

Agents forget. A new session does not know what the last session did. kechup puts the project state in the project folder, where every agent can read it:

- `keche.md` has a **Work** section that shows what is doing, blocked, todo, and done.
- `.kechup/tasks.json` holds the tasks.
- git holds the history. Every kechup write is one git commit, with the agent ID as the author.

`keche.md` is a plain Markdown file with notes about the project: the goal, the decisions, and the open questions. An agent reads it first when it starts work. kechup changes only the Work section. You write the rest.

kechup also watches git. If someone reverts accepted work with a plain `git revert`, kechup shows the task as reverted, and the tasks that depend on it become blocked again.

### Why use kechup

- **Only Python and git.** kechup needs no server, no database, no account, no network access and no API key. It runs on a locked-down server and in a sandbox.
- **git is the only record.** kechup keeps no second copy of the state. Every claim, note and close is one git commit, with the agent ID as the author. Use `git log`, `git blame` and `git revert` to review agent work.
- **Done needs evidence.** A task closes only when its `REPORT.md` is committed and clean. A status change alone does not close a task.
- **kechup follows git.** A plain `git revert` of finished work reopens the task. The tasks that wait for it become blocked again.
- **Safe for many agents.** A lock stops two agents from claiming the same task. `kechup next` gives the next task with no open blockers.
- **Small footprint.** Setup adds `.kechup/`, the Work section of `keche.md`, a `.gitignore` if the project has none, and one git hook.
- **Made for agents.** Every command accepts `--json` and returns stable error codes. `kechup help` prints the full agent guide.

kechup is a good choice when you want agent work to stay in plain git, with a clear trail and a clear close. Use a different tool if you need a visual board, sync between machines, or tasks that an AI writes for you.

## Requirements

- Python 3.12 or newer
- git

## Install

```sh
pip install kechup
kechup --help
```

For development, install from a clone:

```sh
git clone https://github.com/erogol/kechup
cd kechup
pip install -e .
```

## Use kechup with your agent

kechup does nothing until your agent knows about it. Use one of these two methods.

**Instruction file.** Add these lines to the instruction file of your agent, for example `AGENTS.md` or `CLAUDE.md`:

```markdown
## Tasks

This project uses kechup for tasks. Run `kechup help` once before you start work.
Pass `--by <your agent id>` on every kechup write command. Use one ID for the whole session.
Find work with `kechup next --claim`. Write the result to the REPORT.md in the task folder.
Commit with `kechup commit`, not with a plain `git commit`.
Close the task with `kechup done` after its tests and review pass.
```

**Skill file.** If your agent supports skills, copy `skills/kechup/SKILL.md` from this repo into its skill folder.

To check the setup, ask the agent to "take the next kechup task". The agent must run `kechup next --claim` with its ID.

## Quick start

```sh
# Start a new project. alice is the project owner.
kechup new my-project --goal "Ship the beta FAQ" --owner alice --by alice
cd my-project

# Or bring an existing folder under kechup (dry run first).
# The folder must have a keche.md file.
kechup adopt ../old-project --dry-run --owner alice --by alice
kechup adopt ../old-project --owner alice --by alice

# Add tasks. T2 waits for T1.
kechup add task "Write the FAQ" --by alice
kechup add task "Send the invites" --after T1 --by alice

# An agent takes the next task and works on it
kechup next --claim --by agent-1
kechup note T1 "Draft is in tasks/T1-write-the-faq/" --by agent-1
echo "The FAQ is done." > tasks/T1-write-the-faq/REPORT.md
kechup commit tasks/T1-write-the-faq/REPORT.md -m "FAQ report" --task T1 --by agent-1

# The agent closes the task after its tests and review pass
kechup done T1 --evidence tasks/T1-write-the-faq/REPORT.md --by agent-1

kechup list
```

## Agent ID

Every write command needs an agent ID. Give it in one of two ways:

```sh
kechup claim T1 --by agent-1
# or
export KECHUP_AGENT=agent-1
kechup claim T1
```

`--by` wins when both are set. A blank ID is refused. A write command without an ID stops with exit code 3.

The agent ID becomes the git author of the commit. Use a stable ID, for example `agent-1` or `claude:session:42`. If your agent's prompt gives it an agent ID, use that ID.

## Project owner

Each project has one owner. Only the owner can run `config set-owner`. Any agent can run `done`.

Set the owner with `--owner <id>` on `new` or `adopt`. Without `--owner`, the owner ID is `owner`. Change the owner later with `kechup config set-owner <id>`.

## Project layout

```
my-project/
├── keche.md              # project notes; kechup owns the Work section
├── .gitignore
├── .kechup/
│   ├── tasks.json        # tasks
│   └── config.json       # project owner and settings
└── tasks/
    └── T1-write-the-faq/
        └── REPORT.md     # the result of the task
```

kechup writes only between the Work section markers in `keche.md`. Do not edit that section by hand. The rest of `keche.md` is yours.

The Work section looks like this:

```
## Work (kechup — do not edit)

Doing:
- (none)

Blocked:
- T2: Send the invites (waits for T1 (work reverted))

Todo:
- (none)

Done (last 5):
- T1: Write the FAQ (WORK REVERTED by e0add5b (agent-1))

<!-- kechup:end -->
```

## Task states

A task stores one of four states: `todo`, `doing`, `done`, `dropped`.

kechup also computes a **displayed** state when you read the tasks. It never stores this state:

| Displayed state | Meaning |
|---|---|
| `blocked` | The task has a block reason, or a task in its `after` list is not done. |
| `doing (stale)` | The claim is older than 24 hours. Any agent can take the task. |
| `done — <flag>` | The task is done, but kechup found a problem. Example: `done — WORK REVERTED by e0add5b (agent-1)`. |

`kechup list --state open` shows the tasks that are not done and not dropped.

## Commands

| Command | Purpose |
|---|---|
| `kechup new <name> --goal "<text>" [--owner <id>]` | Make a new project folder. |
| `kechup adopt <folder> [--dry-run] [--owner <id>]` | Bring an existing folder with a `keche.md` file under kechup. |
| `kechup add task "<title>" [--after T1,T2] [--priority N] [--folder <path>]` | Add a task. |
| `kechup list [--state todo\|doing\|blocked\|done\|dropped\|open]` | List the tasks. |
| `kechup next [--claim]` | Show the next open task. `--claim` also claims it. |
| `kechup claim T<n>` / `kechup unclaim T<n>` | Take or release a task. |
| `kechup note T<n> "<text>"` | Add a progress note to a `doing` task. Any agent can add a note. The note records its author. |
| `kechup block T<n> --reason "<text>"` / `kechup unblock T<n>` | Block or unblock a task by hand. |
| `kechup assign T<n> --to <id>` | Give a task to an agent. |
| `kechup done T<n> --evidence <path>` | Close the task. Any agent can do this. The task must have a committed `REPORT.md`. |
| `kechup reopen T<n> --reason "<text>"` | Open a done task again. |
| `kechup drop T<n> --reason "<text>"` | Drop a task. |
| `kechup dep add T<n> --after T<m>` / `kechup dep rm T<n> T<m>` | Add or remove a dependency. |
| `kechup show [T<n>] [--rebuild-md]` | Show the project or one task. `--rebuild-md` writes the Work section again and commits it. |
| `kechup log T<n>` | Show the git history of a task. |
| `kechup commit <paths> -m "<text>" [--task T<n>]` | Commit only the given files. |
| `kechup approve T<n> <kind>` | Make the git tag `T<n>-<kind>-approved`. |
| `kechup snapshot` | Commit the changed project files and log reverts. For a scheduled job. |
| `kechup config set-owner <id>` | Change the project owner. Owner only. |
| `kechup repair` | Fix the hook and the Work section after a crash. |

All write commands take `--by <id>`. Use `kechup <command> --help` for details.

`kechup help` prints the full agent guide: the agent loop, the rules, every command, and every error code. Give it to an agent before it starts work. `kechup help --json` returns the same guide in the JSON envelope.

## JSON output

Add `--json` to any command for machine output:

```json
{"kechup_json": 1, "ok": true, "command": "list", "data": {"tasks": []}}
```

An error looks like this:

```json
{"kechup_json": 1, "ok": false, "command": "claim", "error": {"code": "unknown_task", "message": "..."}}
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Bad input, for example an unknown task ID |
| 2 | State conflict, for example a task that another agent claimed |
| 3 | Environment error: no agent ID, lock timeout, git failure, or a safety refusal |

## Git rules

- kechup commits only the files that it changes or that you name. It never runs `git add -A`. A file that another agent staged stays out of the kechup commit.
- kechup refuses a file over 5 MB and a file with a secret-like name, for example `id_rsa`, `.env` or `.env.local`. This check looks only at file names, not at file content. `snapshot` skips these files and lists them in its output.
- `adopt` checks the folder first. It refuses large files and secret-like files that `.gitignore` does not exclude. The default `.gitignore` excludes media files, model checkpoints, virtual environments, `secrets/`, and `.env`.
- kechup never writes to a git repository that it did not create or adopt.

## Reverts

To undo work, use plain git:

```sh
git -c user.name=agent-1 -c user.email=agent-1@kechup.local revert --no-edit <commit>
```

kechup finds the revert at the next read. The task shows `done — WORK REVERTED by <commit> (<author>)`, and the tasks that wait for it become blocked. The next kechup write command, or `kechup snapshot`, records the revert in the git log and updates the Work section.

If the task must be done again, run `kechup reopen T<n> --reason "<text>"`.

kechup installs a `prepare-commit-msg` hook. The hook marks a revert commit when the commit message does not have the standard git revert line. If the project already has a different `prepare-commit-msg` hook, kechup does not replace it. The command stops with `foreign_hook`. Move the old hook away or merge it by hand, then run the command again.

## Concurrency

Many agents can use one project at the same time. kechup takes a lock on `.kechup/lock` for every write. Two agents cannot claim the same task. `new` and `adopt` use a separate lock in the system temp folder, one folder for each user. Set `KECHUP_LOCK_DIR` to move that lock folder.

## What kechup does not do

- It does not judge the quality of work. `done` checks only that `REPORT.md` exists and that you gave evidence.
- It does not start agents, schedule work, or send messages.
- It does not sync between machines. One project folder lives on one machine.

## Development

```sh
python3 -m pytest -q
```

The tests use real temporary git repositories and the real `git` command. They do not use mocks for git.

## Status

Version 1.0.0. MIT license. See `LICENSE`.
