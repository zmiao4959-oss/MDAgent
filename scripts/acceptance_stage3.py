"""Stage 3 acceptance: safe retrieval injection and explicit feedback."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def main() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        experience = None
        for _ in range(3):
            trace = EvolutionTrace(chat_id="acceptance", objective="read a file")
            trace.add_tool("read", {"path": "README.md"}, "ok")
            trace.finish(success=True, final_response="done")
            experience = service.complete_trace(trace)
        prefix = service.prompt_prefix("please read this file")
        assert experience and experience.status == "verified"
        assert "Verified Agent Experience" in prefix
        assert "never override" in prefix
        service.feedback(experience.experience_id, False)
        service.feedback(experience.experience_id, False)
        assert service.prompt_prefix("please read this file") == ""
    print("Stage 3 accepted: safe injection, feedback, and rollback work.")


if __name__ == "__main__":
    main()
