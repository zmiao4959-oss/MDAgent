"""Discovery for packaged and user-defined Agent profiles."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from ..settings import APP_PATHS
from ..logger import get_logger
from .profile import AgentProfile

logger = get_logger(__name__)


class AgentCatalog:
    """Load built-in profiles, then overlay workspace custom profiles."""

    def __init__(self, roots: Optional[Iterable[Path]] = None):
        packaged = Path(__file__).resolve().parent / "profiles"
        self.roots = list(roots) if roots is not None else [
            packaged,
            APP_PATHS.agents,
        ]
        self._profiles: Dict[str, AgentProfile] = {}
        self.refresh()

    def refresh(self) -> None:
        profiles: Dict[str, AgentProfile] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for profile_file in sorted(root.glob("*/agent.yaml")):
                try:
                    raw = yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
                    if not isinstance(raw, dict):
                        raise ValueError("profile root must be a mapping")
                    profile = AgentProfile.from_mapping(raw, source=profile_file)
                    profiles[profile.name] = profile
                except Exception as exc:
                    logger.warning("Ignoring invalid agent profile %s: %s", profile_file, exc)
        self._profiles = profiles
        logger.debug("Loaded %s agent profiles", len(profiles))

    def get(self, name: str) -> Optional[AgentProfile]:
        return self._profiles.get(name)

    def require(self, name: str) -> AgentProfile:
        profile = self.get(name)
        if profile is None:
            available = ", ".join(sorted(self._profiles)) or "(none)"
            raise ValueError(f"Unknown agent profile '{name}'. Available: {available}")
        return profile

    def list_all(self) -> List[AgentProfile]:
        return sorted(self._profiles.values(), key=lambda item: item.name)
