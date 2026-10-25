import re

import pytest
import yaml

from conftest import ROOT

SOURCE = ROOT / "skills"
TARGETS = [ROOT / ".claude" / "skills", ROOT / ".kiro" / "skills"]
SKILLS = sorted(p.parent for p in SOURCE.glob("*/SKILL.md"))
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_skill_follows_agent_skills_spec(skill):
    meta = frontmatter(skill / "SKILL.md")
    assert meta["name"] == skill.name
    assert NAME_RE.match(meta["name"]) and len(meta["name"]) <= 64
    assert 0 < len(meta["description"]) <= 1024


@pytest.mark.parametrize("target", TARGETS, ids=lambda p: p.parent.name)
def test_ide_copies_match_source(target):
    for skill in SKILLS:
        for src in skill.rglob("*"):
            if src.is_file():
                dest = target / src.relative_to(SOURCE)
                assert dest.is_file(), f"{dest} missing, run scripts/sync_skills.py"
                assert dest.read_bytes() == src.read_bytes(), f"{dest} drifted, run scripts/sync_skills.py"


def test_no_non_keyboard_characters_in_skills():
    for path in SOURCE.rglob("*.md"):
        bad = {ch for ch in path.read_text(encoding="utf-8") if ord(ch) > 126}
        assert not bad, f"{path} contains {bad}"
