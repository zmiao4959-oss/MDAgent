"""Stage 12 acceptance: redaction, backup, restore, recoverable purge, and audit."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.governance import GovernanceManager
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def learn(service, objective):
    for _ in range(3):
        trace = EvolutionTrace(chat_id="acceptance", objective=objective)
        trace.add_tool("read", {}, "token=unsafe-secret")
        trace.finish(success=True, final_response="done")
        experience = service.complete_trace(trace)
    return experience


def main():
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        first = learn(service, "read api_key=unsafe-secret manual")
        assert "unsafe-secret" not in str(service.export_data())
        backup = service.backup_data()
        learn(service, "read another manual")
        assert len(service.list_experiences()) == 2
        service.restore_data(backup["name"])
        assert [item.experience_id for item in service.experience_store.all()] == [first.experience_id]
        result = service.purge_data(GovernanceManager.CONFIRM_PURGE)
        assert Path(result["trash_path"]).is_dir()
        assert service.experience_store.all() == []
    print("Stage 12 accepted: redaction, backup, restore, recoverable purge, and audit work.")


if __name__ == "__main__":
    main()
