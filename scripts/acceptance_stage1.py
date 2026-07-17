"""Stage 1 acceptance: persistent execution traces and quality signals."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.trace import EvolutionTrace, TraceStore


def main() -> None:
    with TemporaryDirectory() as directory:
        store = TraceStore(Path(directory) / "traces.jsonl")
        trace = EvolutionTrace(chat_id="acceptance", objective="verify tracing")
        trace.add_tool("read", {"path": "README.md"}, "ok")
        trace.finish(success=True, final_response="done", rounds=1)
        store.append(trace)
        restored = store.recent(1)[0]
        assert restored.success is True
        assert restored.score == 1.0
        assert restored.tool_calls[0].name == "read"
    print("Stage 1 accepted: trace persistence and quality scoring work.")


if __name__ == "__main__":
    main()
