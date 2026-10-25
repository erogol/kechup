"""The full agent guide printed by `kechup help`."""

GUIDE = """\
kechup - a task tracker for one project folder, made for AI agents.

Each project keeps its tasks in .kechup/tasks.json and in git. Every kechup
write is one git commit, with your agent ID as the author. keche.md is the
project notes file. kechup owns only its Work section.

RUN COMMANDS INSIDE THE PROJECT FOLDER
  kechup finds the project from the current folder (the git top level).
  Outside a project, a command stops with error not_a_project (exit 3).
  `new`, `adopt` and `help` work from any folder.

AGENT ID
  Every write command needs an agent ID:
    kechup claim T1 --by <id>
    export KECHUP_AGENT=<id>      # used when --by is not given
  --by wins when both are set. A blank ID stops with no_agent_id (exit 3).
  Use one stable ID for all of your work, for example agent-1 or
  claude:session:42. If your prompt gives you an agent ID, use it.
  These commands need no ID: list, show, log, next (without --claim),
  snapshot, help.

THE AGENT LOOP
  1. Read the state:      kechup list --state open
                          kechup show            (counts + Work section)
  2. Take a task:         kechup next --claim --by <id>
                          or: kechup claim T<n> --by <id>
  3. Read the task:       kechup show T<n>       (folder, notes, after list)
                          Read keche.md. Work in the task folder.
  4. Record progress:     kechup note T<n> "<what you did>" --by <id>
                          Add a note at least once every 24 hours.
  5. Write the result:    <task folder>/REPORT.md
  6. Commit your files:   kechup commit <paths> -m "<text>" --task T<n> --by <id>
  7. Close the task:       After your tests and review pass, run
                          kechup done T<n> --evidence <path> --by <id>
  If you are stuck:       kechup block T<n> --reason "<why>" --by <id>
  If you stop the work:   kechup unclaim T<n> --by <id>

RULES FOR AGENTS
  - Commit with `kechup commit`. Do not use `git add -A` or `git commit -a`.
  - Name only your own files. A file that another agent staged stays out.
  - Do not edit the Work section of keche.md. Do not edit .kechup/ by hand.
  - Run `done` only after the tests and the review pass. Any agent can run it.
  - On exit code 2, read the error and do not retry the same command.
  - On lock_timeout (exit 3), wait a few seconds and run the command again.

TASK STATES
  Stored: todo, doing, done, dropped.
  Displayed (computed when you read, never stored):
    blocked        The task has a block reason, or a task in its after
                   list is not done (or its work was reverted).
    doing (stale)  The claim is older than 24 hours. Any agent can claim it.
    done — <flag>  Done, but kechup found a problem, for example
                   "done — WORK REVERTED by <commit> (<author>)".
  Only the claim owner's own note renews the claim. A note from another
  agent does not.

COMMANDS
  kechup new <name> --goal "<text>" [--owner <id>] --by <id>
      Make a new project folder with git, keche.md and .kechup/.
      The owner ID is "owner" when you do not give --owner.
  kechup adopt <folder> [--dry-run] [--owner <id>] --by <id>
      Bring an existing folder with a keche.md under kechup. Run --dry-run
      first. It stops on large or secret-like files that .gitignore does
      not exclude (unsafe_file).
  kechup add task "<title>" [--after T1,T2] [--priority N] [--folder <path>] --by <id>
      Add a task. The title has 1-200 characters. Priority default is 5.
      A lower number comes first. The default folder is tasks/T<n>-<slug>/.
  kechup list [--state todo|doing|blocked|done|dropped|open]
      List the tasks. "open" means todo, doing or blocked.
  kechup next [--claim --by <id>]
      Show the next task: todo or stale doing, lowest priority number,
      then lowest ID. --claim also claims it. no_open_task when none.
  kechup claim T<n> --by <id>
      Take a todo or stale task. A blocked task stops with
      task_blocked (exit 2); unblock it first. Any other state
      stops with already_claimed (exit 2).
  kechup unclaim T<n> --by <id>
      Give a doing task back to todo. Only the claim owner can do this,
      or any agent when the claim is stale.
  kechup note T<n> "<text>" --by <id>
      Add a note to a doing task. Any agent can add a note.
  kechup block T<n> --reason "<text>" --by <id>
  kechup unblock T<n> --by <id>
      Set or clear the block reason. Not on done or dropped tasks.
  kechup assign T<n> --to <id> --by <id>
      Give the task to an agent. The task becomes doing.
  kechup done T<n> --evidence <path> --by <id>
      Close the task. Any agent can do this. <task folder>/REPORT.md
      must be committed and must not have uncommitted changes.
  kechup reopen T<n> --reason "<text>" --by <id>
      Open a done task again. The reason becomes a note.
  kechup drop T<n> --reason "<text>" --by <id>
      Drop a task that is not done.
  kechup dep add T<n> --after T<m> --by <id>
  kechup dep rm T<n> T<m> --by <id>
      Add or remove a dependency. kechup refuses a cycle.
  kechup show [T<n>] [--rebuild-md --by <id>]
      Show the project or one task. --rebuild-md writes the Work section
      again and commits it.
  kechup log T<n>
      Show the git commits of a task, with reverts.
  kechup commit <paths...> -m "<text>" [--task T<n>] --by <id>
      Commit only these files or folders. It refuses files over 5 MB,
      secret-like names (.env, id_rsa*, *.pem, *.key, *.p12, names with
      "secret" or "credential", files in secrets/), and paths outside the
      project.
  kechup approve T<n> <kind> --by <id>
      Make the git tag T<n>-<kind>-approved. The task folder must be clean.
  kechup snapshot
      Commit all changed project files as author "snapshot". It skips
      large and secret-like files and lists them. For a scheduled job.
  kechup config set-owner <id> --by <owner id>
      Owner only. Change the project owner.
  kechup repair --by <id>
      Fix the git hook and the Work section after a crash.
  kechup help
      Show this guide.

JSON OUTPUT
  Add --json to any command (before or after the command name):
    {"kechup_json": 1, "ok": true, "command": "list", "data": {...}}
    {"kechup_json": 1, "ok": false, "command": "claim",
     "error": {"code": "already_claimed", "message": "..."}}
  Read "ok" and "error.code". Do not parse the text output.

EXIT CODES AND ERRORS
  0  success
  1  bad input: unknown_task, cycle, bad_title, bad_folder, not_in_after,
     no_open_task, exists, no_keche_md, outer_repo
  2  state conflict: already_claimed, task_blocked, not_doing, not_owner,
     not_done, terminal_state, no_report, dirty_report, dirty_tree,
     tag_exists, unsafe_file, foreign_hook
  3  environment: no_agent_id, not_a_project, lock_timeout, unsafe_lock_dir,
     crash_residue, secret_like, too_large, path_escape, not_found,
     git_failure, git_error,
     invalid_tasks, invalid_config, bad_tasks_json, bad_config_json
  crash_residue: an earlier command stopped before its commit.
  Run `kechup repair --by <id>`, then run your command again.

REVERTS
  To undo work, use plain git:
    git revert --no-edit <commit>
  kechup shows the task as "done — WORK REVERTED", and the tasks that wait
  for it become blocked. To do the task again:
    kechup reopen T<n> --reason "<text>" --by <id>
"""
