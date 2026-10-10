"""`kechup help` prints the full agent guide, from any folder."""

from __future__ import annotations

from pathlib import Path

from conftest import run_kechup

from kechup.cli import build_parser
from kechup.guide import GUIDE


def test_help_text_outside_a_project(tmp_dir: Path) -> None:
    result = run_kechup(["help"], cwd=tmp_dir, as_json=False)
    assert result.exit_code == 0, result.stderr
    assert result.stdout == GUIDE
    assert "THE AGENT LOOP" in result.stdout


def test_help_json_envelope(tmp_dir: Path) -> None:
    result = run_kechup(["help"], cwd=tmp_dir)
    assert result.exit_code == 0, result.stderr
    assert result.json is not None
    assert result.json["ok"] is True
    assert result.json["command"] == "help"
    assert result.json["data"]["guide"] == GUIDE


def test_help_needs_no_agent_id(tmp_dir: Path) -> None:
    result = run_kechup(["help", "--json"], cwd=tmp_dir, agent=None)
    assert result.exit_code == 0, result.stderr


def test_guide_names_every_command() -> None:
    parser = build_parser()
    sub_action = next(a for a in parser._actions if a.dest == "command")
    for name in sub_action.choices:
        assert f"kechup {name}" in GUIDE, name


def test_guide_names_every_error_code() -> None:
    root = Path(__file__).resolve().parent.parent / "kechup"
    import re

    codes: set[str] = set()
    pattern = re.compile(r'KechupError\(\s*[0-9],\s*"([a-z_]+)"', re.S)
    for source in root.glob("*.py"):
        codes.update(pattern.findall(source.read_text(encoding="utf-8")))
    codes.discard("unknown_command")
    missing = sorted(code for code in codes if code not in GUIDE)
    assert not missing, missing
