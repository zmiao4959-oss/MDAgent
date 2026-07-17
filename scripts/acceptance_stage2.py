"""Stage 2 acceptance: evidence-based experience promotion and rejection."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.experience import ExperienceEngine, ExperienceStore
from miniclaw.learning.trace import EvolutionTrace


def main() -> None:
    with TemporaryDirectory() as directory:
        store = ExperienceStore(Path(directory) / "experiences.db")
        engine = ExperienceEngine(store)
        experience = None
        for _ in range(3):
            trace = EvolutionTrace(chat_id="acceptance", objective="inspect a file")
            trace.add_tool("read", {"path": "README.md"}, "ok")
            trace.finish(success=True, final_response="done")
            experience = engine.observe_trace(trace)
        assert experience and experience.status == "verified"
        assert store.verified_for("read file")
        store.record_feedback(experience.experience_id, positive=False)
        rejected = store.record_feedback(experience.experience_id, positive=False)
        assert rejected and rejected.status == "rejected"
    print("Stage 2 accepted: evidence promotion, retrieval, and rejection work.")


if __name__ == "__main__":
    main()
