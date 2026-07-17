"""Stage 8 acceptance: safe structured reflection and approval workflow."""
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace
from miniclaw.llm.base import LLMResponse


class FakeLLM:
    async def chat(self, messages, **kwargs):
        assert "unsafe-secret" not in messages[1].content
        return LLMResponse(content=json.dumps({
            "situation": "validated LAMMPS execution",
            "lesson": "Validate the input file and working directory before execution.",
            "failure_pattern": "invalid input",
            "scope": ["execute", "lammps"],
            "confidence": 0.9,
        }))


def trace() -> EvolutionTrace:
    item = EvolutionTrace(
        chat_id="acceptance",
        objective="run LAMMPS token=unsafe-secret",
    )
    item.add_tool("execute", {"command": "lammps"}, "completed")
    item.finish(success=True, final_response="done")
    return item


async def run() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        current = None
        for _ in range(3):
            current = trace()
            experience = service.complete_trace(current)
        assert experience and experience.status == "verified"

        proposal = await service.generate_reflection(current, FakeLLM(), model="fake")
        assert proposal and proposal["status"] == "proposed"
        assert service.reflections("proposed")
        reviewed = service.review_reflection(proposal["reflection_id"], approve=True)
        assert reviewed and reviewed.status == "verified"
        assert reviewed.lesson.startswith("Validate the input file")
        assert service.reflections("approved")
    print("Stage 8 accepted: redacted structured reflection, human review, and re-evaluation work.")


if __name__ == "__main__":
    asyncio.run(run())
