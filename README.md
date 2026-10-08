# kechup

kechup is a command-line task tracker for one project folder. Any
agent — Keche, Claude Code, or a person — can see open tasks, claim one,
and report it done. kechup uses the Python standard library and the
`git` binary only.

## Install (no install needed for tests)

```
cd /opt/kechup
python3 -m kechup --help
```

A `pyproject.toml` console script `kechup` is also defined, for a real
install (`pip install -e .`).

## Agent identity

Every write command needs an agent id. Give it one of two ways:

```
kechup claim T1 --by keche:mission:7f3c
# or
export KECHUP_AGENT=keche:mission:7f3c
kechup claim T1
```

`--by` wins when both are set. A write command with neither refuses:
exit code 3.

## States

A task's stored state is one of `todo`, `doing`, `done`, `dropped`.
kechup also computes a **displayed** state at read time, never stored:

- `blocked` — a manual block reason, or an unmet `after` dependency.
- `doing (stale)` — claimed more than 24 hours ago, open to any agent.
- `done — <flag>` — the task shows done, but kechup found a problem:
  a git revert of the accepted work, a history rewrite that lost the
  accepted commit, or a missing `REPORT.md`.

`kechup list` and `kechup show T<n>` show the displayed state.

## Commands

```
kechup new <name> --goal "<text>" [--owner <id>]
kechup adopt <folder> [--dry-run] [--owner <id>]
kechup add task "<title>" [--after T3,T4] [--priority N] [--folder <path>]
kechup list [--state todo|doing|blocked|done|dropped|open]
kechup next [--claim]
kechup claim T<n>
kechup unclaim T<n>
kechup note T<n> "<text>"
kechup block T<n> --reason "<text>"
kechup unblock T<n>
kechup assign T<n> --to <agent id>
kechup done T<n> --evidence "<path-or-ref>"   # project owner only
kechup reopen T<n> --reason "<text>"
kechup drop T<n> --reason "<text>"
kechup dep add T<n> --after T<m>
kechup dep rm T<n> T<m>
kechup show [T<n>] [--rebuild-md]
kechup log T<n>
kechup commit <path> [<path> ...] -m "<text>" [--task T<n>]
kechup approve T<n> <kind>
kechup snapshot
kechup config set-owner <id>
kechup repair
```

Add `--json` (before or after the subcommand) for the machine envelope:

```json
{"kechup_json": 1, "ok": true, "command": "list", "data": {}}
```

Exit codes: `0` success, `1` bad input, `2` state conflict, `3`
environment error (no agent id, lock timeout, git failure, safety
refusal, foreign repo).

## A typical agent workflow

```
kechup next --claim --by keche:mission:7f3c
# ... do the work, write tasks/T3-slug/REPORT.md ...
kechup commit tasks/T3-slug/REPORT.md -m "write the report" --task T3 --by keche:mission:7f3c
kechup done T3 --evidence tasks/T3-slug/REPORT.md --by owner   # project owner only
```

## What kechup does not do

- kechup does not judge work quality. `done` only checks that
  `REPORT.md` exists and an evidence string was given.
- kechup does not schedule or dispatch work, start missions, or start
  sessions.
- kechup does not sync across machines. A project folder is local to
  one machine.
- kechup never writes to a git repo it did not create or adopt.

See `SPEC.md` in the gated-build folder for the full specification.
