"""Test helper: runs `kechup claim` with a crash point, for the
crash-recovery tests. Not part of the kechup package. Works on the
base branch too (no journal module there yet), so the red/green proof
is a real process crash on both sides.

KILL_POINT selects where the process hangs until it is SIGKILLed:
  before_write   inside the tasks.json atomic write, before the replace
                 (guarded files still clean)
  before_commit  after the guarded file writes and `git add`, before
                 the commit (dirty tree, no commit)
  after_commit   after the real commit, before the journal clear
                 (clean tree; no-op on the base branch)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import kechup.gitutil as gitutil_mod  # noqa: E402
import kechup.model as model_mod  # noqa: E402

try:
    import kechup.journal as journal_mod
except ImportError:  # base branch: no journal module yet
    journal_mod = None

kill_point = os.environ.get("KILL_POINT", "before_commit")
SLEEP_SECONDS = 15

if kill_point == "before_write":
    def _slow_atomic_write_text(path: Path, text: str) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        time.sleep(SLEEP_SECONDS)
        os.replace(tmp_path, path)

    model_mod.atomic_write_text = _slow_atomic_write_text
elif kill_point == "before_commit":
    _real_commit = gitutil_mod.commit

    def _slow_commit(*args, **kwargs):
        time.sleep(SLEEP_SECONDS)
        return _real_commit(*args, **kwargs)

    gitutil_mod.commit = _slow_commit
elif kill_point == "after_commit":
    if journal_mod is None:
        # Base branch: no journal phase exists; the claim ends right
        # after the commit. Sleep so the kill point is still observable.
        _real_commit = gitutil_mod.commit

        def _slow_commit(*args, **kwargs):
            result = _real_commit(*args, **kwargs)
            time.sleep(SLEEP_SECONDS)
            return result

        gitutil_mod.commit = _slow_commit
    else:
        # The clean-tree guard branch also calls journal.clear at
        # command start, before any record. Sleep only after the write
        # path recorded its journal, so the kill point stays after the
        # commit and the guard keeps its normal speed.
        _recorded = False
        _real_record = journal_mod.record

        def _flagged_record(*args, **kwargs):
            global _recorded
            _recorded = True
            return _real_record(*args, **kwargs)

        journal_mod.record = _flagged_record

        _real_clear = journal_mod.clear

        def _slow_clear(*args, **kwargs):
            if _recorded:
                time.sleep(SLEEP_SECONDS)
                return None
            return _real_clear(*args, **kwargs)

        journal_mod.clear = _slow_clear
else:
    raise SystemExit(f"unknown KILL_POINT: {kill_point}")

if __name__ == "__main__":
    project = sys.argv[1]
    task_id = sys.argv[2]
    os.chdir(project)
    os.environ["KECHUP_AGENT"] = "crash-agent"
    argv = ["--json", "claim", task_id]
    from kechup.cli import main  # noqa: E402

    sys.exit(main(argv))