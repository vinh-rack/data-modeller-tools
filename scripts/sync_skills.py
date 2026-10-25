"""Copy skills/* into each IDE's skills folder. skills/ is the only place to edit.

Claude Code reads .claude/skills/, Cursor also reads .claude/skills/ (compatibility path), Kiro reads .kiro/skills/.
Run: uv run python scripts/sync_skills.py
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills"
TARGETS = [ROOT / ".claude" / "skills", ROOT / ".kiro" / "skills"]


def sync() -> list[Path]:
    written = []
    names = sorted(p.name for p in SOURCE.iterdir() if (p / "SKILL.md").is_file())
    for target in TARGETS:
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            dest = target / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(SOURCE / name, dest)
            written.append(dest)
        for stale in target.iterdir():
            if stale.is_dir() and stale.name.startswith("dma-") and stale.name not in names:
                shutil.rmtree(stale)
    return written


if __name__ == "__main__":
    for p in sync():
        print("synced", p.relative_to(ROOT))
    sys.exit(0)
