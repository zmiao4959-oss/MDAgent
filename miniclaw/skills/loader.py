"""
skills/loader.py - skill discovery and loading.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..config import WORKSPACE_DIR
from ..logger import get_logger
from ..tools.paths import skill_roots

logger = get_logger(__name__)


@dataclass
class SkillInfo:
    name: str
    description: str
    location: Path
    skill_dir: Path


class SkillLoader:
    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or WORKSPACE_DIR / "skills"
        self._cache: Dict[str, SkillInfo] = {}
        self._refresh()

    def _roots_to_scan(self) -> List[Path]:
        default_root = WORKSPACE_DIR / "skills"
        if self.skills_dir != default_root:
            return [self.skills_dir]
        return list(skill_roots())

    def _refresh(self):
        """Scan skill directories and cache metadata."""
        self._cache.clear()
        for root in self._roots_to_scan():
            if not root.exists():
                continue
            for skill_dir in root.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                content = skill_file.read_text(encoding="utf-8")
                name_match = re.search(r"<name>(.*?)</name>", content, re.DOTALL)
                desc_match = re.search(r"<description>(.*?)</description>", content, re.DOTALL)
                if not name_match:
                    continue
                name = name_match.group(1).strip()
                description = (
                    re.sub(r"\s+", " ", desc_match.group(1)).strip()
                    if desc_match else ""
                )
                self._cache[name] = SkillInfo(
                    name=name,
                    description=description,
                    location=skill_file,
                    skill_dir=skill_dir,
                )
        logger.debug("Loaded %s skills", len(self._cache))

    def list_all(self) -> List[SkillInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> Optional[SkillInfo]:
        return self._cache.get(name)

    def load_full(self, name: str) -> Optional[str]:
        info = self.get(name)
        if info:
            return info.location.read_text(encoding="utf-8")
        return None

    def match_by_query(self, user_query: str) -> List[SkillInfo]:
        query_lower = user_query.lower()
        matched = []
        for info in self._cache.values():
            score = 0
            if info.name.lower() in query_lower:
                score += 10
            for word in re.findall(r"\w+", info.description.lower()):
                if word in query_lower:
                    score += 2
            if score > 0:
                matched.append((score, info))
        matched.sort(key=lambda x: x[0], reverse=True)
        return [info for _, info in matched[:3]]
