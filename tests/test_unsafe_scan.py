"""Tests for the F1 fix: project-only ignore policy for the unsafe-file
scan (`kechup/bootstrap.py` `_scan_for_unsafe_files`). Real temp git repos,
real git binary, no mocks."""

from __future__ import annotations

import tempfile as tempfile_mod
from pathlib import Path

from conftest import git, git_as, run_kechup

SIX_MB = 6 * 1024 * 1024


def _fake_git_failing_on(tmp_dir: Path, substring: str) -> Path:
    """A `git` wrapper on PATH that fails only the call whose argument
    list contains `substring`, otherwise forwards to the real git binary
    (R3 test: "a bad git binary in PATH for the test", FIX_SPEC_F3)."""
    import shutil as shutil_mod

    real_git = shutil_mod.which("git")
    assert real_git is not None, "git binary not found on PATH"
    fake_dir = tmp_dir / "fake-git-bin"
    fake_dir.mkdir()
    script = fake_dir / "git"
    script.write_text(
        "#!/bin/sh\n"
        f'case " $* " in\n  *" {substring} "*)\n'
        '    echo "fake git failure: $*" >&2\n'
        "    exit 1\n    ;;\nesac\n"
        f'exec "{real_git}" "$@"\n'
    )
    script.chmod(0o755)
    return fake_dir


def test_adopt_scan_git_failure_refuses_not_silently_empty(tmp_dir: Path) -> None:
    """R3: a git failure during the unsafe-file scan must refuse (exit 3,
    code git_error), never be treated as an empty (safe) scan. Forces the
    untracked-file listing (`ls-files --others`) to fail; the new oversized
    file would normally only be caught through that call."""
    import os

    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "big.bin").write_bytes(b"0" * SIX_MB)
    fake_dir = _fake_git_failing_on(tmp_dir, "ls-files --others")
    env = {"PATH": f"{fake_dir}:{os.environ['PATH']}"}
    result = run_kechup(["adopt", str(target)], cwd=tmp_dir, agent="owner", extra_env=env)
    assert result.exit_code == 3, result.stdout
    assert result.json["error"]["code"] == "git_error"
    assert not (target / ".kechup").exists()


def test_ignored_mp4_over_5mb_does_not_block_adopt(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "big.mp4").write_bytes(b"0" * SIX_MB)
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 0, result.stderr


def test_ignored_file_under_nested_gitignore_does_not_block(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    # A top .gitignore means the default template does not apply, so the
    # nested .gitignore must do the hiding on its own.
    (target / ".gitignore").write_text("*.log\n")
    sub = target / "data"
    sub.mkdir()
    (sub / ".gitignore").write_text("*.bin\n")
    (sub / "big.bin").write_bytes(b"0" * SIX_MB)
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 0, result.stderr


def test_nonignored_pdf_over_5mb_blocks_with_reason(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "paper.pdf").write_bytes(b"0" * SIX_MB)
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "unsafe_file"
    unsafe = result.json["data"]["unsafe"]
    assert len(unsafe) == 1
    assert unsafe[0]["path"] == "paper.pdf"
    assert unsafe[0]["reason"] == "over 5 MB"
    assert unsafe[0]["bytes"] == SIX_MB
    assert unsafe[0]["tracked"] is False


def test_secrets_dir_stays_out_of_scope(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    secrets_dir = target / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "x").write_bytes(b"0" * SIX_MB)
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 0, result.stderr


def test_nonignored_id_rsa_blocks_with_secret_like_reason(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "id_rsa").write_text("private key material")
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    unsafe = result.json["data"]["unsafe"]
    assert unsafe[0]["path"] == "id_rsa"
    assert unsafe[0]["reason"] == "secret-like"
    assert unsafe[0]["tracked"] is False


def test_global_excludes_file_does_not_hide_unsafe_file(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "paper.pdf").write_bytes(b"0" * SIX_MB)

    global_excludes = tmp_dir / "global-excludes"
    global_excludes.write_text("*.pdf\n")
    global_gitconfig = tmp_dir / "global-gitconfig"
    global_gitconfig.write_text(f"[core]\n\texcludesFile = {global_excludes}\n")

    result = run_kechup(
        ["adopt", str(target), "--dry-run"],
        cwd=tmp_dir,
        agent="owner",
        extra_env={"GIT_CONFIG_GLOBAL": str(global_gitconfig)},
    )
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "unsafe_file"
    assert result.json["data"]["unsafe"][0]["path"] == "paper.pdf"


def test_existing_repo_info_exclude_does_not_hide_unsafe_file(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    git(target, "init", "-q", "-b", "main")
    (target / "keche.md").write_text("# proj\n")
    (target / "paper.pdf").write_bytes(b"0" * SIX_MB)
    info_dir = target / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "exclude").write_text("*.pdf\n")
    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    assert result.json["error"]["code"] == "unsafe_file"
    assert result.json["data"]["unsafe"][0]["path"] == "paper.pdf"


def test_tracked_but_ignored_unsafe_file_blocks_with_index_advice(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    git(target, "init", "-q", "-b", "main")
    (target / "keche.md").write_text("# proj\n")
    big = target / "big.mp4"
    big.write_bytes(b"0" * SIX_MB)
    git(target, "add", "big.mp4", "keche.md")
    git_as(target, "owner", "commit", "-q", "-m", "add big tracked file")
    (target / ".gitignore").write_text("*.mp4\n")

    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    unsafe = result.json["data"]["unsafe"]
    assert unsafe[0]["path"] == "big.mp4"
    assert unsafe[0]["tracked"] is True
    message = result.json["error"]["message"]
    assert "Remove it from the index" in message
    assert "the file stays on disk" in message
    assert "git rm --cached" in message
    assert "and add the path to .gitignore" not in message


def test_tracked_unsafe_file_not_ignored_gets_combined_advice(tmp_dir: Path) -> None:
    target = tmp_dir / "proj"
    target.mkdir()
    git(target, "init", "-q", "-b", "main")
    (target / "keche.md").write_text("# proj\n")
    paper = target / "paper.pdf"
    paper.write_bytes(b"0" * SIX_MB)
    git(target, "add", "paper.pdf", "keche.md")
    git_as(target, "owner", "commit", "-q", "-m", "add big tracked file")

    result = run_kechup(["adopt", str(target), "--dry-run"], cwd=tmp_dir, agent="owner")
    assert result.exit_code == 2
    unsafe = result.json["data"]["unsafe"]
    assert unsafe[0]["path"] == "paper.pdf"
    assert unsafe[0]["tracked"] is True
    message = result.json["error"]["message"]
    assert "Remove it from the index" in message
    assert "and add the path to .gitignore" in message


def test_scan_leaves_target_byte_identical(tmp_dir: Path) -> None:
    from kechup.bootstrap import _scan_for_unsafe_files

    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")
    (target / "note.txt").write_text("hello")

    before = {
        p.relative_to(target): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
    }
    _scan_for_unsafe_files(target)
    after = {
        p.relative_to(target): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
    }
    assert before == after
    assert not (target / ".git").exists()


def test_scan_removes_temp_git_dir(tmp_dir: Path, monkeypatch: object) -> None:
    from kechup import bootstrap

    target = tmp_dir / "proj"
    target.mkdir()
    (target / "keche.md").write_text("# proj\n")

    created: list[Path] = []
    original_mkdtemp = tempfile_mod.mkdtemp

    def recording_mkdtemp(*args: object, **kwargs: object) -> str:
        path = original_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(bootstrap.tempfile, "mkdtemp", recording_mkdtemp)
    bootstrap._scan_for_unsafe_files(target)
    assert len(created) == 1
    assert not created[0].exists()
