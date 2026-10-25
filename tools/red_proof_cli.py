"""Behavioral red proof: the SAME real interruption scenario against
the BASE code and the CANDIDATE code.

Scenario (identical on both sides): a real `kechup claim` subprocess is
SIGKILLed between the guarded file writes and the commit; then a second
real `kechup claim` runs.

Expected: base -> exit 3 crash_residue (the original failure).
          candidate -> exit 0 (automatic recovery).

No mocks. Real git repositories, real processes, real SIGKILL.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
HELPER = WORKTREE / "tests" / "helpers" / "crash_claim.py"
GUARDED = [".kechup/tasks.json", ".kechup/config.json", "keche.md"]


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return out.stdout


def run_kechup(src: str, argv: list[str], cwd: Path, agent: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = src
    env["KECHUP_AGENT"] = agent
    out = subprocess.run(
        [sys.executable, "-m", "kechup", "--json", *argv],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    return out


def run_scenario(src: str, tmp: Path, label: str, helper: Path) -> bool:
    project = tmp / label
    r = run_kechup(src, ["new", str(project), "--goal", "red proof",
                         "--owner", "owner"], cwd=tmp, agent="owner")
    assert r.returncode == 0, r.stderr
    t1 = run_kechup(src, ["add", "task", "one"], cwd=project,
                    agent="owner").stdout
    t2 = run_kechup(src, ["add", "task", "two"], cwd=project,
                    agent="owner").stdout
    import json as _json
    t1 = _json.loads(t1)["data"]["id"]
    t2 = _json.loads(t2)["data"]["id"]

    env = dict(os.environ)
    env["PYTHONPATH"] = src
    env["KILL_POINT"] = "before_commit"
    proc = subprocess.Popen(
        [sys.executable, str(helper), str(project), t1],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if git(project, "status", "--porcelain", "--", *GUARDED).strip():
            break
        time.sleep(0.05)
    else:
        proc.kill()
        raise AssertionError("residue never appeared")
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    result = run_kechup(src, ["claim", t2], cwd=project, agent="agent-b")
    body = result.stdout
    print(f"[{label}] second claim exit_code={result.returncode}")
    print(f"[{label}] stdout head: {body.strip().splitlines()[:1]}")
    if result.returncode != 0:
        print(f"[{label}] stderr: {result.stderr.strip()[:300]}")
    return result.returncode


def main() -> int:
    # Candidate: this worktree.
    base_dir = WORKTREE / "base-src"
    if base_dir.exists():
        subprocess.run(["rm", "-rf", str(base_dir)], check=True)
    base_dir.mkdir()
    archive = subprocess.run(
        ["git", "archive", "0b7bc91"], cwd=WORKTREE, capture_output=True, check=True
    )
    subprocess.run(["tar", "-x", "-C", str(base_dir)], input=archive.stdout, check=True)
    # The crash helper works on the base branch too (journal import is
    # guarded); it must live inside the base tree so it imports base code.
    helpers_dir = base_dir / "tests" / "helpers"
    helpers_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(HELPER), str(helpers_dir / HELPER.name))
    base_helper = base_dir / "tests" / "helpers" / HELPER.name

    try:
        with tempfile.TemporaryDirectory(prefix="kechup-red-") as raw:
            tmp = Path(raw)
            rc_base = run_scenario(str(base_dir), tmp, "base", base_helper)
        with tempfile.TemporaryDirectory(prefix="kechup-red-") as raw:
            tmp = Path(raw)
            rc_candidate = run_scenario(str(WORKTREE), tmp, "candidate", HELPER)
    finally:
        subprocess.run(["rm", "-rf", str(base_dir)], check=True)

    print(f"base second claim exit_code={rc_base} "
          f"(3 = original crash_residue refusal, expected)")
    print(f"candidate second claim exit_code={rc_candidate} (0 = recovery, expected)")
    ok = rc_base == 3 and rc_candidate == 0
    print(f"RED PROOF: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())