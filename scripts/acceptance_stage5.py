"""Stage 5 acceptance: experience provenance, outcome scoring, and auto rollback."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def successful_read_trace() -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="acceptance", objective="read a file")
    trace.add_tool("read", {"path": "README.md"}, "ok")
    trace.finish(success=True, final_response="done")
    return trace


def main() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        experience = None
        for _ in range(3):
            experience = service.complete_trace(successful_read_trace())
        assert experience and experience.status == "verified"

        for expected_usage in (1, 2):
            _, applied_ids = service.prompt_context("read this file")
            assert applied_ids == [experience.experience_id]
            trace = EvolutionTrace(
                chat_id="acceptance",
                objective="read a file",
                metadata={"applied_experience_ids": applied_ids},
            )
            trace.finish(success=False, error="result verification failed")
            service.complete_trace(trace, learn=False, evaluate_applied=True)
            current = service.experience_store.all()[0]
            assert current.usage_count == expected_usage

        assert current.status == "rejected"
        assert service.prompt_prefix("read this file") == ""
    print("Stage 5 accepted: provenance, usage tracking, outcome scoring, and auto rollback work.")


if __name__ == "__main__":
    main()
