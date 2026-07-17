import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.source_agent import LLMSourcePatchAgent
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore
from miniclaw.llm.base import LLMResponse


def _run(repo, *args):
    return subprocess.run(
        list(args), cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "package").mkdir(parents=True)
    (repo / "package" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "package" / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    _run(repo, "git", "init", "-b", "main")
    _run(repo, "git", "config", "user.email", "agent@example.invalid")
    _run(repo, "git", "config", "user.name", "Source Agent Test")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-m", "baseline")
    return repo


def _proposal(store, *, suspected_files=None):
    proposal = SourceProposal(
        title="Fix addition", problem_signature="value|addition",
        error_category="value-error", evidence_run_ids=["1", "2", "3"],
        source_projects=["a", "b"],
        suspected_files=suspected_files if suspected_files is not None else ["package/calc.py"],
        sanitized_examples=["expected seven"], hypothesis="operator is wrong",
        expected_benefit="correct addition", risk="low", rollback_plan="revert",
    )
    store.upsert(proposal)
    store.update_status(proposal.proposal_id, "approved")
    return proposal


def _test_plan(path="tests/test_evolution_generated_calc.py", content=None):
    return json.dumps({
        "summary": "reproduce wrong addition",
        "edits": [{
            "operation": "create", "path": path,
            "content": content or (
                "from package.calc import add\n\n"
                "def test_addition_regression():\n    assert add(5, 2) == 7\n"
            ),
        }],
    })


def _patch_plan(old="return left - right"):
    return json.dumps({
        "summary": "use addition",
        "edits": [{
            "operation": "replace", "path": "package/calc.py",
            "old": old, "new": "return left + right",
        }],
    })


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1].content)
        return LLMResponse(content=self.responses.pop(0))


def test_automated_agent_generates_test_first_candidate(tmp_path):
    repo = _repo(tmp_path)
    workspace = tmp_path / "workspace"
    service = EvolutionService(workspace, shared_workspace=workspace)
    proposal = _proposal(service._source_proposals())
    llm = FakeLLM(_test_plan(), _patch_plan())
    baseline = (repo / "package" / "calc.py").read_text(encoding="utf-8")

    experiment = asyncio.run(
        service.run_automated_source_experiment(proposal.proposal_id, repo, llm)
    )

    assert experiment["status"] == "patched"
    assert experiment["reproduction_results"][0]["returncode"] != 0
    assert experiment["targeted_results"][0]["returncode"] == 0
    assert len(llm.prompts) == 2
    assert "generated regression test" in llm.prompts[1]
    assert (repo / "package" / "calc.py").read_text(encoding="utf-8") == baseline
    assert _run(repo, "git", "status", "--porcelain") == ""


def test_invalid_json_is_rejected_before_source_change(tmp_path):
    repo = _repo(tmp_path)
    store = SourceProposalStore(tmp_path / "source.db")
    proposal = _proposal(store)
    agent = LLMSourcePatchAgent(FakeLLM("not-json"))

    with pytest.raises(ValueError, match="invalid JSON"):
        asyncio.run(agent.write_reproduction_test(repo, proposal))
    assert _run(repo, "git", "status", "--porcelain") == ""


def test_generated_test_path_and_content_are_restricted(tmp_path):
    repo = _repo(tmp_path)
    store = SourceProposalStore(tmp_path / "source.db")
    proposal = _proposal(store)

    with pytest.raises(PermissionError, match="repository-relative"):
        asyncio.run(LLMSourcePatchAgent(
            FakeLLM(_test_plan("../tests/test_evolution_generated_escape.py"))
        ).write_reproduction_test(repo, proposal))

    unsafe = _test_plan(content="import subprocess\n\ndef test_bad():\n    subprocess.run([])\n")
    with pytest.raises(PermissionError, match="unsafe content"):
        asyncio.run(
            LLMSourcePatchAgent(FakeLLM(unsafe)).write_reproduction_test(repo, proposal)
        )


def test_patch_requires_one_exact_match_in_suspected_file(tmp_path):
    repo = _repo(tmp_path)
    store = SourceProposalStore(tmp_path / "source.db")
    proposal = _proposal(store)
    agent = LLMSourcePatchAgent(FakeLLM(_test_plan(), _patch_plan("left")))
    asyncio.run(agent.write_reproduction_test(repo, proposal))

    with pytest.raises(ValueError, match="exactly once"):
        asyncio.run(agent.implement_patch(repo, proposal))


def test_patch_cannot_modify_generated_reproduction_test(tmp_path):
    repo = _repo(tmp_path)
    store = SourceProposalStore(tmp_path / "source.db")
    proposal = _proposal(store)
    patch = json.dumps({
        "summary": "weaken test",
        "edits": [{
            "operation": "replace",
            "path": "tests/test_evolution_generated_calc.py",
            "old": "assert add(5, 2) == 7",
            "new": "assert True",
        }],
    })
    agent = LLMSourcePatchAgent(FakeLLM(_test_plan(), patch))
    asyncio.run(agent.write_reproduction_test(repo, proposal))

    with pytest.raises(PermissionError, match="outside suspected files"):
        asyncio.run(agent.implement_patch(repo, proposal))


def test_proposal_without_safe_suspected_file_cannot_reach_llm(tmp_path):
    repo = _repo(tmp_path)
    store = SourceProposalStore(tmp_path / "source.db")
    proposal = _proposal(store, suspected_files=["../outside.py"])
    llm = FakeLLM(_test_plan())

    with pytest.raises(PermissionError, match="repository-relative"):
        asyncio.run(LLMSourcePatchAgent(llm).write_reproduction_test(repo, proposal))
    assert llm.prompts == []
