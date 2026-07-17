"""Stage 23 acceptance: all managed evolution artifacts share governance."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miniclaw.learning.governance import GovernanceManager
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.source_experiment import SourceExperimentStore
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore


def main():
    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        service = EvolutionService(workspace, shared_workspace=workspace)
        root = workspace / ".miniclaw" / "evolution"
        database = root / "source-evolution.db"
        proposals = SourceProposalStore(database)
        SourceExperimentStore(database)
        proposal = SourceProposal(
            title="Fix", problem_signature="failure|fix", error_category="runtime-error",
            evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
            suspected_files=["app.py"], sanitized_examples=["failure"],
            hypothesis="bug", expected_benefit="fix", risk="low", rollback_plan="revert",
        )
        proposals.upsert(proposal)
        old_skill = root / "skill-drafts" / "old"
        old_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_text("# original\n", encoding="utf-8")
        (old_skill / "metadata.json").write_text(
            json.dumps({"status": "rejected", "created_at": 1}), encoding="utf-8"
        )
        policy = root / "executable-policies" / "approved" / "safe-policy"
        policy.mkdir(parents=True)
        (policy / "policy.json").write_text("{}", encoding="utf-8")

        backup = service.backup_data()
        (old_skill / "SKILL.md").write_text("changed\n", encoding="utf-8")
        database.unlink()
        restored = service.restore_data(backup["name"])
        assert restored["restored"] == backup["name"]
        assert proposals.__class__(database).get(proposal.proposal_id) is not None
        assert (old_skill / "SKILL.md").read_text(encoding="utf-8") == "# original\n"
        assert (policy / "policy.json").is_file()

        preview = service.cleanup_evolution_artifacts(
            retention_days=30, dry_run=True, now=100 * 86400
        )
        assert [item["path"] for item in preview["targets"]] == ["skill-drafts/old"]
        assert old_skill.is_dir()
        cleaned = service.cleanup_evolution_artifacts(
            retention_days=30,
            dry_run=False,
            confirmation=GovernanceManager.CONFIRM_CLEANUP,
            now=100 * 86400,
        )
        assert cleaned["removed"] == ["skill-drafts/old"]
        assert not old_skill.exists()
        assert (policy / "policy.json").is_file()
    print("Stage 23 accepted: managed evolution assets back up, restore, and clean safely.")


if __name__ == "__main__":
    main()
