"""
skills/loader.py — Skill 加载与匹配
"""
import re
from pathlib import Path
from typing import List, Dict, Optional
from ..config import WORKSPACE_DIR
from ..logger import get_logger
from dataclasses import dataclass

logger = get_logger(__name__)


@dataclass
class SkillInfo:
    name: str
    description: str
    location: Path          # SKILL.md 实际路径
    skill_dir: Path         # Skill 根目录


class SkillLoader:
    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or WORKSPACE_DIR / "skills"
        self._cache: Dict[str, SkillInfo] = {}
        self._refresh()
    
    def _refresh(self):
        """扫描 skills 目录"""
        self._cache.clear()
        if not self.skills_dir.exists():
            return
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text(encoding="utf-8")
            name_match = re.search(r'<name>(.*?)</name>', content)
            desc_match = re.search(r'<description>(.*?)</description>', content)
            if name_match:
                self._cache[name_match.group(1).strip()] = SkillInfo(
                    name=name_match.group(1).strip(),
                    description=desc_match.group(1).strip() if desc_match else "",
                    location=skill_file,
                    skill_dir=skill_dir,
                )
        logger.debug(f"Loaded {len(self._cache)} skills")
    
    def list_all(self) -> List[SkillInfo]:
        return list(self._cache.values())
    
    def get(self, name: str) -> Optional[SkillInfo]:
        return self._cache.get(name)
    
    def load_full(self, name: str) -> Optional[str]:
        """加载 SKILL.md 的完整内容"""
        info = self.get(name)
        if info:
            return info.location.read_text(encoding="utf-8")
        return None
    
    def match_by_query(self, user_query: str) -> List[SkillInfo]:
        """根据用户查询匹配相关 Skill（简单关键词匹配）"""
        query_lower = user_query.lower()
        matched = []
        for info in self._cache.values():
            score = 0
            # 名称匹配权重最高
            if info.name.lower() in query_lower:
                score += 10
            # 描述关键词匹配
            for word in re.findall(r'\w+', info.description.lower()):
                if word in query_lower:
                    score += 2
            if score > 0:
                matched.append((score, info))
        matched.sort(key=lambda x: x[0], reverse=True)
        return [info for _, info in matched[:3]]