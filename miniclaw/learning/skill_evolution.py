"""Synthesize validated experiences into reviewable, never-auto-installed skills."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .experience import Experience


@dataclass
class SkillDraft:
    name: str
    description: str
    task_pattern: str
    experience_ids: List[str]
    path: str
    status: str = "draft"
    conflicts: List[str] = field(default_factory=list)
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


class SkillSynthesizer:
    NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    FORBIDDEN = re.compile(
        r"ignore\s+(?:previous|prior)\s+instructions|bypass\s+safety|"
        r"disable\s+(?:security|approval)|reveal\s+(?:system|developer)\s+prompt",
        re.IGNORECASE,
    )

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.drafts_root = self.workspace / ".miniclaw" / "evolution" / "skill-drafts"
        self.skills_root = self.workspace / "skills"

    def synthesize(
        self,
        experiences: List[Experience],
        *,
        task_pattern: str,
        min_experiences: int = 2,
    ) -> SkillDraft:
        selected = [
            item for item in experiences
            if item.status == "verified" and item.task_pattern == task_pattern
        ]
        if len(selected) < min_experiences:
            raise ValueError(
                f"requires at least {min_experiences} verified experiences for one task pattern"
            )
        name = _skill_name(task_pattern)
        description = (
            f"Apply validated MiniClaw workflows for {task_pattern}. "
            f"Use when a task matches: {task_pattern}."
        )[:500]
        conflicts = self.detect_conflicts(name)
        draft_id = uuid.uuid4().hex
        path = self.drafts_root / draft_id / "SKILL.md"
        lessons = list(dict.fromkeys(item.lesson.strip() for item in selected if item.lesson.strip()))
        body = [
            "---",
            f"name: {name}",
            f"description: {description}",
            "---",
            "",
            f"# {name}",
            "",
            "## Workflow",
            "",
        ]
        body.extend(
            f"{index}. Apply this validated strategy: {lesson}"
            for index, lesson in enumerate(lessons, 1)
        )
        body.extend([
            "",
            "## Safety",
            "",
            "- Follow user instructions and existing approval requirements.",
            "- Verify observable results before reporting completion.",
            "- Stop and report unexpected errors instead of repeating unsafe actions.",
            "",
        ])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(body), encoding="utf-8")
        draft = SkillDraft(
            draft_id=draft_id,
            name=name,
            description=description,
            task_pattern=task_pattern,
            experience_ids=[item.experience_id for item in selected],
            path=str(path),
            conflicts=conflicts,
        )
        self._save_metadata(draft)
        return draft

    def validate(self, draft_id: str) -> Dict:
        draft = self.get(draft_id)
        if draft is None:
            raise FileNotFoundError("skill draft not found")
        path = Path(draft.path)
        text = path.read_text(encoding="utf-8")
        errors = []
        if not self.NAME_RE.fullmatch(draft.name) or len(draft.name) > 64:
            errors.append("invalid skill name")
        frontmatter = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not frontmatter:
            errors.append("missing YAML frontmatter")
        else:
            keys = {
                line.split(":", 1)[0].strip()
                for line in frontmatter.group(1).splitlines() if ":" in line
            }
            if keys != {"name", "description"}:
                errors.append("frontmatter must contain only name and description")
        if len(text.splitlines()) > 500:
            errors.append("SKILL.md exceeds 500 lines")
        if self.FORBIDDEN.search(text):
            errors.append("skill contains unsafe instructions")
        if draft.conflicts:
            errors.append("skill name conflicts with an installed skill")
        return {"valid": not errors, "errors": errors, "draft": draft.to_dict()}

    def isolated_test(self, draft_id: str) -> Dict:
        validation = self.validate(draft_id)
        if not validation["valid"]:
            return {"passed": False, "errors": validation["errors"]}
        draft = self.get(draft_id)
        assert draft is not None
        with tempfile.TemporaryDirectory(prefix="miniclaw-skill-test-") as directory:
            isolated = Path(directory) / draft.name
            isolated.mkdir()
            shutil.copy2(draft.path, isolated / "SKILL.md")
            copied = (isolated / "SKILL.md").read_text(encoding="utf-8")
            passed = f"name: {draft.name}" in copied and "## Workflow" in copied
        draft.status = "tested" if passed else "draft"
        self._save_metadata(draft)
        return {"passed": passed, "errors": [] if passed else ["isolated copy failed"]}

    def approve(self, draft_id: str, confirmation: str) -> Dict:
        draft = self.get(draft_id)
        if draft is None:
            raise FileNotFoundError("skill draft not found")
        if confirmation != draft.name:
            raise PermissionError("confirmation must exactly match the skill name")
        result = self.isolated_test(draft_id)
        if not result["passed"]:
            raise ValueError("skill draft did not pass validation")
        destination = self.skills_root / draft.name
        if destination.exists() or self.detect_conflicts(draft.name):
            raise FileExistsError("an installed skill already uses this name")
        destination.mkdir(parents=True)
        shutil.copy2(draft.path, destination / "SKILL.md")
        draft.status = "approved"
        self._save_metadata(draft)
        return {"installed": True, "path": str(destination), "draft": draft.to_dict()}

    def reject(self, draft_id: str) -> Dict:
        draft = self.get(draft_id)
        if draft is None:
            raise FileNotFoundError("skill draft not found")
        draft.status = "rejected"
        self._save_metadata(draft)
        return draft.to_dict()

    def detect_conflicts(self, name: str) -> List[str]:
        conflicts = []
        if not self.skills_root.exists():
            return conflicts
        for skill_file in self.skills_root.glob("*/SKILL.md"):
            text = skill_file.read_text(encoding="utf-8", errors="replace")[:2000]
            match = re.search(r"(?m)^name:\s*([^\s]+)", text)
            if skill_file.parent.name == name or (match and match.group(1).strip() == name):
                conflicts.append(str(skill_file))
        return conflicts

    def list_drafts(self) -> List[Dict]:
        if not self.drafts_root.exists():
            return []
        drafts = []
        for metadata in self.drafts_root.glob("*/metadata.json"):
            try:
                drafts.append(json.loads(metadata.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(drafts, key=lambda item: item.get("created_at", 0), reverse=True)

    def get(self, draft_id: str) -> Optional[SkillDraft]:
        metadata = self.drafts_root / Path(draft_id).name / "metadata.json"
        if not metadata.is_file():
            return None
        return SkillDraft(**json.loads(metadata.read_text(encoding="utf-8")))

    def _save_metadata(self, draft: SkillDraft) -> None:
        metadata = Path(draft.path).parent / "metadata.json"
        metadata.write_text(
            json.dumps(draft.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _skill_name(task_pattern: str) -> str:
    words = re.findall(r"[a-z0-9]+", task_pattern.lower())
    base = "-".join(words[:6]) or "validated-workflow"
    name = f"handle-{base}"[:64].rstrip("-")
    return name if name else "handle-validated-workflow"
