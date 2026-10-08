"""Locking (spec section 6): the project lock and the bootstrap lock."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kechup.errors import KechupError

LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 0.1


@contextmanager
def _flock_exclusive(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise KechupError(
                        3,
                        "lock_timeout",
                        "project is locked by another kechup process, try again",
                    )
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def project_lock(project_root: Path) -> Iterator[None]:
    """The project lock: `.kechup/lock`, held for every write command."""
    lock_path = project_root / ".kechup" / "lock"
    with _flock_exclusive(lock_path):
        yield


def bootstrap_lock_path(target: Path) -> Path:
    """The bootstrap lock path: a lock-dir file keyed by the target's
    resolved absolute path (section 6). `KECHUP_LOCK_DIR` overrides the
    base directory; tests use it to keep the system temp dir clean.
    Lock files are never deleted while in use (deleting a file that
    another process locks breaks the lock rule)."""
    resolved = str(target.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    base = os.environ.get("KECHUP_LOCK_DIR")
    if base:
        return Path(base) / f"{digest}.lock"
    return Path(tempfile.gettempdir()) / "kechup-locks" / f"{digest}.lock"


@contextmanager
def bootstrap_lock(target: Path) -> Iterator[None]:
    """The bootstrap lock, serializing two `new`/`adopt` calls on one path."""
    with _flock_exclusive(bootstrap_lock_path(target)):
        yield
