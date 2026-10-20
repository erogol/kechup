"""The kechup skill doc has a short description and no stale agent-id text."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL_PATH = ROOT / "skills" / "kechup" / "SKILL.md"


def test_skill_description_is_short() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    assert match, "no description line in SKILL.md frontmatter"
    assert len(match.group(1)) <= 60


def test_guide_has_neutral_agent_id_example() -> None:
    from kechup.guide import GUIDE

    assert "agent-1" in GUIDE


def test_public_docs_use_neutral_agent_ids() -> None:
    from kechup.guide import GUIDE

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")
    for text in (GUIDE, readme, skill):
        assert "agent-1" in text
