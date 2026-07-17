"""Stage 18 acceptance: repeated cross-project failures create one sanitized source proposal."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.source_proposal import RepeatedFailureDetector, SourceProposalStore
from miniclaw.learning.trace import EvolutionTrace


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "repo"
        source = repo / "miniclaw" / "agent.py"
        source.parent.mkdir(parents=True)
        source.write_text("# source\n", encoding="utf-8")
        traces = []
        for project, line in (("a", 10), ("a", 11), ("b", 12)):
            trace = EvolutionTrace(
                chat_id=f"chat:{project}", objective="repeat failure",
                metadata={"project_id": project},
            )
            trace.finish(
                success=False,
                error=f'File "{source}", line {line}\nValueError: api_key=unsafe-secret item {line}',
            )
            traces.append(trace)
        store = SourceProposalStore(root / "source-evolution.db")
        detector = RepeatedFailureDetector(store, repo)
        first = detector.scan(traces)
        second = detector.scan(traces)
        assert len(first) == len(second) == len(store.list()) == 1
        assert first[0].proposal_id == second[0].proposal_id
        assert "unsafe-secret" not in str(first[0].to_dict())
        assert first[0].suspected_files == ["miniclaw/agent.py"]
    print("Stage 18 accepted: repeated failures yield one private, auditable source proposal.")


if __name__ == "__main__":
    main()
