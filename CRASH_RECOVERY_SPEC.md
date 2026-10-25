# kechup crash-residue automatic recovery — SPEC (v2, advisor-approved shape)

## 1. Problem

Every kechup write command does, under the project flock:
write tasks.json/config.json/keche.md → `git add` → `git commit`.
If the process dies between the file write and the commit, the guarded
paths (`.kechup/tasks.json`, `.kechup/config.json`, `keche.md`) stay
dirty. `check_no_crash_residue` (kechup/guard.py) then refuses every
later write with exit 3 `crash_residue` until a human runs
`kechup repair`. Recurrence: trace 8d8cc0e2, 2026-10-24 14:58:56 UTC.

The guard is correct to be fail-closed: a dirty tree does not show who
wrote the change, whether the write was complete, or whether a writer
is still active. Automatic recovery needs new stored evidence.

## 2. Trust boundary

Concurrent writers that honor the kechup project lock serialize through
it. A journal observed while the lock is held belongs to a kechup
writer that is no longer running (a live writer holds the lock).
Concurrent mutation by tools that ignore the lock (a text editor, raw
git) is outside the supported threat model, EXCEPT that the recovery
predicate refuses any residue such a tool can verifiably leave: a
content mismatch with the journal, a foreign staged index entry, a
moved HEAD, or a dirty unjournaled path all stay refused. Fabrication
of a journal file by a local writer is outside the supported model.

## 3. Evidence: a write-ahead intent journal

A journal file inside the git dir (`kechup-journal.json` directly
under `git rev-parse --git-dir`; never tracked, invisible to
`git status`). Under the project lock, a write command records its
full intended transaction before it touches the first guarded file:

```json
{
  "version": 1,
  "base_head": "<full sha of HEAD at record time>",
  "agent": "<agent id>",
  "message": "<exact commit message the command will use>",
  "paths": {"<guarded rel path>": "<exact final file content, utf-8>"}
}
```

Content is computed before any file write with the same serializers
the writes use, so journaled bytes equal written bytes. Journal keys
are exactly: version, base_head, agent, message, paths. `paths` keys
must be a non-empty subset of the guarded paths. Any deviation is
invalid.

Crash-safe publication: write to `<gitdir>/kechup-journal.json.tmp`,
fsync, then `os.replace`. The journal is removed only after the commit
is confirmed (commit helper returned). A journal is never overwritten
while present: record refuses if one exists (that state is residue).

## 4. Recovery predicate (fail-closed)

At `begin_write`, under the project lock:

A. Guarded paths dirty → recovery only if ALL hold:
   1. Journal exists and validates against the exact schema.
   2. `journal.base_head` == current HEAD.
   3. Every dirty guarded path is a journaled path.
   4. EVERY journaled path's on-disk bytes equal the journaled
      intended bytes (the write phase completed for the whole
      transaction). No partial write is completed or replayed.
   5. Index safety: for each journaled path with a staged entry that
      differs from HEAD (mode or blob), the staged blob equals the
      journaled intended bytes. Any foreign staged content, or an
      unexpected index entry (type/stage), refuses. Unrelated index
      entries are untouched: recovery commits with `git add` +
      `git commit --only` on the journaled paths only, same mechanism
      the normal write path uses.
   Any failure → the same exit 3 `crash_residue` refusal as today.

B. Guarded paths clean + journal present → the only reachable states
   are "commit landed" (HEAD holds the intended bytes), "transaction
   never started" (nothing was written), or "a human already destroyed
   the state" (e.g. `git reset --hard`). In each case no recoverable
   data remains. Action: remove the journal and proceed.

## 5. Recovery action (dirty case)

1. `git add` the journaled paths; `git commit --only` them with
   author = journaled agent and message = journaled message — the
   exact commit the interrupted command would have made.
2. Remove the journal.
3. The invoking command proceeds; its own write re-records its own
   journal normally.

`kechup repair` stays as the manual fallback and clears a present
journal after committing. Bootstrap recovery in `new`/`adopt` is out
of scope. A user-driven `kechup commit` of guarded paths stays
unjournaled and fail-closed.

## 6. Refusal matrix

| State | Result |
|---|---|
| dirty, no journal | refuse `crash_residue` (unchanged) |
| dirty, journal invalid (schema/paths/extra keys) | refuse |
| dirty, journal version not the integer 1 | refuse |
| dirty, journal with a duplicate JSON key | refuse |
| dirty, `base_head` != HEAD | refuse |
| dirty guarded path not journaled | refuse |
| journaled path, on-disk bytes != journal bytes | refuse |
| journaled path is a symlink, or has an exec bit | refuse |
| journaled path, foreign staged index content | refuse |
| journaled path, index entry missing but path exists at HEAD | refuse |
| clean, journal present | clear journal, proceed |
| clean, no journal | proceed (unchanged) |

## 7. Touched sites

- New `kechup/journal.py`: record/clear/validate/recover.
- `kechup/model.py`: `serialize_tasks`, `serialize_config` extracted
  from save_tasks/save_config (byte-identical output).
- `kechup/worksection.py`: `build_keche_md_text` extracted from
  rewrite_keche_md (byte-identical output).
- `kechup/commands.py`: `_write_through`, `show --rebuild-md`,
  `config_set_owner` record a journal around their write+commit.
- `kechup/revertlog.py`: journal its keche.md write+commit.
- `kechup/guide.py`: one sentence on automatic recovery.
- Tests only for the above.

No dependency changes, no other refactors.

## 8. Tests (red on base, green on candidate)

1. Real SIGKILL after the file writes, before commit → next command
   auto-recovers; commit author = original agent; tree clean; journal
   gone; task state correct.
2. Real SIGKILL after journal record, before any file write → clean
   state, journal cleared, next command proceeds; nothing committed.
3. Crash residue + human edit of tasks.json (bytes != journal) →
   still `crash_residue`.
4. Hand-made dirty tree, no journal → still `crash_residue`
   (existing test unchanged).
5. Invalid journal JSON → refuse.
6. Journal `base_head` != HEAD with dirty tree → refuse.
7. Foreign dirty guarded path not in journal → refuse.
8. Foreign staged content on a journaled path → refuse.
9. Kill after commit, before journal removal (clean + journal) → next
   command clears the journal and succeeds.
10. Live concurrent writer keeps `lock_timeout`; after SIGKILL of the
    writer, the second command recovers and its own write succeeds.
11. Full base vs candidate suite: no new failing test IDs.
12. Real CLI interruption + concurrency evidence outside pytest,
    recorded in the branch report.