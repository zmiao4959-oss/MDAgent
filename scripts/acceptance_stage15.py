"""Stage 15 acceptance: semantic, privacy-safe workflows drive structured prompt guidance."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def trace(root, suffix, command):
    item = EvolutionTrace(chat_id="stage15", objective="validate deployment configuration")
    item.add_tool("read", {"path": str(root / f"private{suffix}"), "token": "unsafe-secret"}, "ok")
    item.add_tool("execute", {"command": command}, "completed")
    item.finish(success=True, final_response="validated")
    return item


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        service = EvolutionService(root)
        yaml_item = service.complete_trace(trace(root, ".yaml", "python check.py"))
        lammps_item = service.complete_trace(trace(root, ".in", "lmp -in private.in"))
        assert yaml_item.experience_id != lammps_item.experience_id
        assert "unsafe-secret" not in str(yaml_item.strategy)
        for _ in range(2):
            yaml_item = service.complete_trace(trace(root, ".yaml", "python check.py"))
        assert yaml_item.status == "verified"
        prompt = service.prompt_prefix("validate deployment configuration")
        assert all(label in prompt for label in ("Conditions:", "Workflow:", "Validation:", "Fallback:"))
    print("Stage 15 accepted: semantic structured strategies are private, distinct, and injectable.")


if __name__ == "__main__":
    main()
