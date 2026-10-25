# REPORT — crash-residue automatic recovery (mission ff379da0d9b5)

Task: interrupted kechup writes left uncommitted `.kechup/tasks.json`
changes; every later write failed with `crash_residue` until manual
`kechup repair` (trace 8d8cc0e2, 2026-10-24 14:58:56 UTC).

## Root cause

Every kechup write command does, under the project flock: write guarded
files, `git add`, `git commit`. A process killed between write and
commit leaves the guarded paths dirty. `check_no_crash_residue`
(kechup/guard.py) then refuses every later write. The dirty tree alone
proves no ownership or completeness, so the old guard could not recover
safely.

## Fix

A write-ahead intent journal inside the git dir
(`kechup-journal.json`), plus a strict recovery predicate:

- `kechup/journal.py` (new): `record`, strict `_load`, `recover`,
  `clear`. Every write command records its full intended transaction
  before it touches the first guarded file.
- Recovery runs only when the journal proves a complete, kechup-owned
  transaction: same HEAD (`base_head`), exact on-disk bytes, regular
  non-executable files (no symlinks in any component), no dirty
  guarded path outside the journal, index safe (our own staged bytes or
  HEAD content; conflict stages and foreign staged blobs refuse), and
  the project lock held (so no other lock-honoring kechup writer runs).
- Everything else keeps the fail-closed `crash_residue` refusal and
  the journal stays on disk as evidence.
- Recovery commits with the journaled author and message: the exact
  commit the interrupted command would have made. No replay of partial
  writes ever happens.
- Guard: dirty tree → recover or refuse; clean tree + stale journal →
  clear the journal and proceed.
- `kechup repair` also clears the journal; `guide.py` documents the new
  behavior.
- Trust boundary: the flock proves only that no other lock-honoring
  kechup writer runs. Lock-ignoring tools are outside the model, except
  that every residue they can verifiably leave (bytes mismatch, foreign
  staged entry, moved HEAD, symlink, exec bit, dirty unjournaled path)
  stays refused.

## Gates

1. Spec: CRASH_RECOVERY_SPEC.md (v2), advisor-reviewed before code.
2. Red proof, base 0b7bc91: the 12-test crash suite fails (no journal
   module). Behavioral red proof (tools/red_proof_cli.py): the SAME
   real SIGKILL scenario, base second claim exits 3 `crash_residue`,
   candidate second claim exits 0. PASS.
3. Crash suite: 18 passed (real processes, real SIGKILL, no mocks).
   Covers recovery, no-replay, human edit, invalid JSON, schema
   violations, moved HEAD, unjournaled dirty path, foreign staged
   content, staged deletion, single stage-3 conflict entry, symlink,
   exec bit, after-commit journal clear, live-writer lock_timeout +
   recovery, manual repair, version=True/1.0, duplicate JSON key.
4. Full suites: base 218 passed, candidate 236 passed, zero failures
   on both, no new failing IDs. Run after the rebase onto build/v1
   0b7bc91.
5. Real CLI evidence (tools/cli_crash_evidence.py): 3/3 scenarios PASS
   — SIGKILL recovery, concurrent-writer lock_timeout then recovery,
   corrupted residue refused + manual repair.
6. Advisor full-diff review: round 1 BLOCK (symlink/mode, missing
   index entry, incomplete diff), round 2 BLOCK (single nonzero-stage
   entry), round 3 APPROVE. All findings fixed and tested.

## Commits (branch mission/ff379da0d9b5)

- 29d15ab crash residue: automatic recovery for proven owned transactions
- 8002c42 crash residue: close worktree-type and index holes found in review
- 6141bcd docs: add review-fix refusal rows to crash recovery spec
- 093c721 docs: state the exact lock trust boundary in crash recovery spec
- 4bf7bdf crash residue: refuse a single nonzero-stage index entry
- (this report + evidence tools)

Author policy: GitHub no-reply email
`1402048+erogol@users.noreply.github.com`, per repo convention.

## Safety

No secret touched. No force push. No bot restart needed: the installed
wrapper (`/usr/local/bin/kechup`, PYTHONPATH=/opt/kechup) picks the new
code up on the next CLI process after the merge.