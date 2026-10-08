"""Test helper: runs `kechup claim` but pauses after the tasks.json.tmp
write, before the atomic os.replace, so a SIGKILL mid-write can be tested.
Not part of the kechup package; used only by test_concurrency.py."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kechup import model as model_mod  # noqa: E402

_real_atomic_write_text = model_mod.atomic_write_text


def _slow_atomic_write_text(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    import os

    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    time.sleep(5)
    os.replace(tmp_path, path)


model_mod.atomic_write_text = _slow_atomic_write_text

from kechup.cli import main  # noqa: E402

if __name__ == "__main__":
    project = sys.argv[1]
    task_id = sys.argv[2]
    import os

    os.chdir(project)
    os.environ["KECHUP_AGENT"] = "slow-agent"
    sys.exit(main(["claim", task_id]))
