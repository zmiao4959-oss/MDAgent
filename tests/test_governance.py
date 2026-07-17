from pathlib import Path
import json
import subprocess
import time
import zipfile

import pytest

from miniclaw.learning.governance import GovernanceManager
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.source_experiment import SourceExperiment, SourceExperimentStore
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore
from miniclaw.learning.trace import EvolutionTrace


def _learn(service, objective):
    for _ in range(3):
        trace = EvolutionTrace(chat_id="c", objective=objective)
        trace.add_tool("read", {}, "api_key=super-secret")
        trace.finish(success=True, final_response="token=super-secret")
        experience = service.complete_trace(trace)
    return experience


def test_export_is_redacted_and_audited(tmp_path):
    service = EvolutionService(tmp_path)
    _learn(service, "read api_key=super-secret documentation")

    exported = service.export_data(redact=True)
    rendered = str(exported)

    assert "super-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert service.audit_events()[0]["action"] == "export"


def test_backup_restore_and_checksum_workflow(tmp_path):
    service = EvolutionService(tmp_path)
    first = _learn(service, "read first documentation")
    backup = service.backup_data()
    _learn(service, "read second unrelated manual")
    assert len(service.list_experiences()) == 2

    restored = service.restore_data(backup["name"])

    assert restored["restored"] == backup["name"]
    experiences = service.experience_store.all()
    assert len(experiences) == 1 and experiences[0].experience_id == first.experience_id
    assert restored["pre_restore_backup"] != backup["name"]


def test_purge_requires_confirmation_and_is_recoverable(tmp_path):
    service = EvolutionService(tmp_path)
    _learn(service, "read documentation")

    with pytest.raises(PermissionError):
        service.purge_data("yes")
    result = service.purge_data(GovernanceManager.CONFIRM_PURGE)

    trash = Path(result["trash_path"])
    assert result["recoverable"] is True and trash.is_dir()
    assert service.experience_store.all() == []
    assert service.audit_events()[0]["action"] == "purge"


def test_backup_restores_source_database_skills_and_executable_policies(tmp_path):
    service = EvolutionService(tmp_path, shared_workspace=tmp_path)
    evolution_root = tmp_path / ".miniclaw" / "evolution"
    source_database = evolution_root / "source-evolution.db"
    proposals = SourceProposalStore(source_database)
    proposal = SourceProposal(
        title="Fix", problem_signature="failure|fix", error_category="runtime-error",
        evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
        suspected_files=["app.py"], sanitized_examples=["failure"],
        hypothesis="bug", expected_benefit="fix", risk="low", rollback_plan="revert",
    )
    proposals.upsert(proposal)
    SourceExperimentStore(source_database)
    skill = evolution_root / "skill-drafts" / "draft-one"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# draft\n", encoding="utf-8")
    (skill / "metadata.json").write_text(
        json.dumps({
            "status": "approved", "created_at": 1, "name": "generated-skill"
        }), encoding="utf-8"
    )
    installed = tmp_path / "skills" / "generated-skill"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("# installed\n", encoding="utf-8")
    user_skill = tmp_path / "skills" / "user-skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("# user\n", encoding="utf-8")
    policy = evolution_root / "executable-policies" / "drafts" / "policy-one"
    policy.mkdir(parents=True)
    (policy / "policy.json").write_text("{}", encoding="utf-8")
    (policy / "metadata.json").write_text(
        json.dumps({"status": "draft", "created_at": 1}), encoding="utf-8"
    )

    backup = service.backup_data()
    source_database.unlink()
    (skill / "SKILL.md").write_text("changed\n", encoding="utf-8")
    (installed / "SKILL.md").write_text("changed installed\n", encoding="utf-8")
    (policy / "policy.json").write_text('{"changed": true}', encoding="utf-8")
    restored = service.restore_data(backup["name"])

    assert SourceProposalStore(source_database).get(proposal.proposal_id) is not None
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "# draft\n"
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "# installed\n"
    assert (user_skill / "SKILL.md").read_text(encoding="utf-8") == "# user\n"
    assert (policy / "policy.json").read_text(encoding="utf-8") == "{}"
    assert set(restored["directories"]) == {
        "skill-drafts", "executable-policies", "installed-skills/generated-skill"
    }
    artifacts = {item["path"] for item in service.managed_evolution_artifacts()}
    assert "source-evolution.db" in artifacts
    assert "skill-drafts/draft-one/SKILL.md" in artifacts
    assert "installed-skills/generated-skill/SKILL.md" in artifacts


def test_restore_rejects_undeclared_and_unsafe_archive_paths(tmp_path):
    service = EvolutionService(tmp_path, shared_workspace=tmp_path)
    backup_dir = tmp_path / ".miniclaw" / "evolution" / "backups"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", json.dumps({"schema_version": 2, "files": {}}))
        handle.writestr("../outside.txt", "unsafe")

    with pytest.raises(ValueError, match="unsafe paths"):
        service.restore_data(archive.name)
    assert not (tmp_path / "outside.txt").exists()


def test_restore_rejects_checksum_tampering(tmp_path):
    service = EvolutionService(tmp_path, shared_workspace=tmp_path)
    backup_dir = tmp_path / ".miniclaw" / "evolution" / "backups"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "tampered.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("experiences.db", "not a database")
        handle.writestr("manifest.json", json.dumps({
            "schema_version": 2,
            "files": {"experiences.db": "0" * 64},
        }))

    with pytest.raises(ValueError, match="checksum failed"):
        service.restore_data(archive.name)


def test_cleanup_is_dry_run_confirmed_and_preserves_approved_assets(tmp_path):
    service = EvolutionService(tmp_path, shared_workspace=tmp_path)
    root = tmp_path / ".miniclaw" / "evolution"
    for relative, status in (
        ("skill-drafts/old-skill", "rejected"),
        ("skill-drafts/approved-skill", "approved"),
        ("executable-policies/drafts/old-policy", "draft"),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "metadata.json").write_text(
            json.dumps({"status": status, "created_at": 1}), encoding="utf-8"
        )

    preview = service.cleanup_evolution_artifacts(
        retention_days=30, dry_run=True, now=100 * 86400
    )
    assert len(preview["targets"]) == 2
    assert (root / "skill-drafts/old-skill").is_dir()
    assert service.audit_events() == []
    with pytest.raises(PermissionError, match="confirmation"):
        service.cleanup_evolution_artifacts(
            retention_days=30, dry_run=False, confirmation="yes", now=100 * 86400
        )

    cleaned = service.cleanup_evolution_artifacts(
        retention_days=30,
        dry_run=False,
        confirmation=GovernanceManager.CONFIRM_CLEANUP,
        now=100 * 86400,
    )
    assert len(cleaned["removed"]) == 2
    assert not (root / "skill-drafts/old-skill").exists()
    assert not (root / "executable-policies/drafts/old-policy").exists()
    assert (root / "skill-drafts/approved-skill").is_dir()


def test_cleanup_removes_only_terminal_managed_git_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "governance@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Governance"], cwd=repo, check=True)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)

    service = EvolutionService(tmp_path / "workspace", shared_workspace=tmp_path / "workspace")
    root = tmp_path / "workspace" / ".miniclaw" / "evolution"
    worktree = root / "source-evolution" / "worktrees" / "terminal"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "codex/evolution-cleanup", str(worktree)],
        cwd=repo, check=True, capture_output=True,
    )
    store = SourceExperimentStore(root / "source-evolution.db")
    experiment = SourceExperiment(
        proposal_id="proposal", baseline_commit="baseline",
        branch="codex/evolution-cleanup", worktree=str(worktree), status="failed",
    )
    store.save(experiment, "failed")

    preview = service.cleanup_evolution_artifacts(
        retention_days=30, dry_run=True, source_repo=repo,
        now=time.time() + 31 * 86400,
    )
    assert [item["kind"] for item in preview["targets"]] == ["source-worktree"]
    service.cleanup_evolution_artifacts(
        retention_days=30, dry_run=False,
        confirmation=GovernanceManager.CONFIRM_CLEANUP,
        source_repo=repo, now=time.time() + 31 * 86400,
    )
    assert not worktree.exists()
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/codex/evolution-cleanup"],
        cwd=repo, capture_output=True,
    ).returncode == 0
