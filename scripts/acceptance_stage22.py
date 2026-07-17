"""Stage 22 acceptance: an LLM can produce one constrained test-first candidate."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.source_proposal import SourceProposal
from miniclaw.llm.base import LLMResponse


def command(repo, *args):
    return subprocess.run(args, cwd=repo, check=True, text=True, capture_output=True).stdout


class FakeLLM:
    def __init__(self, service):
        self.service = service
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content=json.dumps({
                "summary": "reproduce",
                "edits": [{
                    "operation": "create",
                    "path": "tests/test_evolution_generated_value.py",
                    "content": "from app import value\n\ndef test_value():\n    assert value() == 2\n",
                }],
            }))
        experiment = self.service.list_source_experiments()[0]
        assert experiment["status"] == "patching"
        assert experiment["reproduction_results"][0]["returncode"] != 0
        return LLMResponse(content=json.dumps({
            "summary": "fix",
            "edits": [{
                "operation": "replace", "path": "app.py",
                "old": "return 1", "new": "return 2",
            }],
        }))


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        command(repo, "git", "init", "-b", "main")
        command(repo, "git", "config", "user.email", "acceptance@example.invalid")
        command(repo, "git", "config", "user.name", "Acceptance")
        command(repo, "git", "add", ".")
        command(repo, "git", "commit", "-m", "baseline")

        workspace = root / "workspace"
        service = EvolutionService(workspace, shared_workspace=workspace)
        proposal = SourceProposal(
            title="Fix value", problem_signature="value|wrong", error_category="value-error",
            evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
            suspected_files=["app.py"], sanitized_examples=["wrong value"],
            hypothesis="constant is wrong", expected_benefit="correct value",
            risk="low", rollback_plan="revert",
        )
        service._source_proposals().upsert(proposal)
        service.review_source_proposal(proposal.proposal_id, approve=True)
        llm = FakeLLM(service)
        experiment = asyncio.run(
            service.run_automated_source_experiment(proposal.proposal_id, repo, llm)
        )
        assert experiment["status"] == "patched"
        assert experiment["reproduction_results"][0]["returncode"] != 0
        assert experiment["targeted_results"][0]["returncode"] == 0
        assert llm.calls == 2
        assert "return 1" in (repo / "app.py").read_text(encoding="utf-8")
        assert command(repo, "git", "status", "--porcelain") == ""
    print("Stage 22 accepted: constrained LLM patching remains test-first and isolated.")


if __name__ == "__main__":
    main()
