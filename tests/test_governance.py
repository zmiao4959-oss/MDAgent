from pathlib import Path

import pytest

from miniclaw.learning.governance import GovernanceManager
from miniclaw.learning.service import EvolutionService
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
