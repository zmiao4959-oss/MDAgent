from miniclaw.learning.source_proposal import RepeatedFailureDetector, SourceProposalStore
from miniclaw.learning.trace import EvolutionTrace


def _failed(project_id, error):
    trace = EvolutionTrace(
        chat_id=f"chat:{project_id}",
        objective="run repeated source task",
        metadata={"project_id": project_id},
    )
    trace.finish(success=False, error=error)
    return trace


def test_repeated_cross_project_failures_create_one_sanitized_proposal(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "miniclaw" / "agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("# source\n", encoding="utf-8")
    traces = [
        _failed("a", f'File "{source}", line {line}, in run\nValueError: token=super-secret invalid value {line}')
        for line in (10, 11)
    ]
    traces.append(
        _failed("b", f'File "{source}", line 12, in run\nValueError: token=super-secret invalid value 12')
    )
    store = SourceProposalStore(tmp_path / "source-evolution.db")

    proposals = RepeatedFailureDetector(store, repo).scan(traces)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.error_category == "value-error"
    assert proposal.source_projects == ["a", "b"]
    assert proposal.suspected_files == ["miniclaw/agent.py"]
    assert len(proposal.evidence_run_ids) == 3
    rendered = str(proposal.to_dict())
    assert "super-secret" not in rendered
    assert str(repo) not in rendered
    assert "[REDACTED]" in rendered


def test_scanning_same_failures_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    traces = [
        _failed("a", f"Timeout while processing job {index}")
        for index in (100, 101)
    ] + [_failed("b", "Timeout while processing job 102")]
    store = SourceProposalStore(tmp_path / "source-evolution.db")
    detector = RepeatedFailureDetector(store, repo)

    first = detector.scan(traces)
    second = detector.scan(traces)

    assert first[0].proposal_id == second[0].proposal_id
    assert len(store.list()) == 1
    assert len(store.get(first[0].proposal_id).evidence_run_ids) == 3
    assert store.events(first[0].proposal_id)[0]["action"] == "evidence_updated"


def test_failure_threshold_and_project_diversity_are_required(tmp_path):
    store = SourceProposalStore(tmp_path / "source-evolution.db")
    detector = RepeatedFailureDetector(store, tmp_path)

    assert detector.scan([
        _failed("a", "TypeError: mismatch 1"),
        _failed("a", "TypeError: mismatch 2"),
        _failed("a", "TypeError: mismatch 3"),
    ]) == []
    assert detector.scan([
        _failed("a", "TypeError: mismatch 1"),
        _failed("b", "TypeError: mismatch 2"),
    ]) == []
    assert store.list() == []


def test_external_stack_paths_are_not_recorded(tmp_path):
    store = SourceProposalStore(tmp_path / "source-evolution.db")
    detector = RepeatedFailureDetector(store, tmp_path / "repo")
    traces = [
        _failed(project, f'File "C:/external/library.py", line {index}\nKeyError: missing {index}')
        for project, index in (("a", 1), ("a", 2), ("b", 3))
    ]

    proposal = detector.scan(traces)[0]

    assert proposal.suspected_files == []
    assert "C:/external" not in str(proposal.to_dict())
