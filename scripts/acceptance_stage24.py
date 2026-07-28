"""Stage 24 acceptance: research capsules capture reproducible evidence safely."""
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miniclaw.learning.trace import EvolutionTrace
from miniclaw.research_capsule import ResearchCapsuleManager


def main() -> None:
    with TemporaryDirectory() as directory:
        workspace = Path(directory) / "project"
        workspace.mkdir()
        result = workspace / "result.dat"
        result.write_bytes(b"scientific-result\n")
        (workspace / ".env").write_text("API_KEY=private", encoding="utf-8")

        trace = EvolutionTrace(chat_id="webchat:stage24", objective="capture")
        trace.add_tool(
            "execute",
            {"command": "simulate API_KEY=private", "token": "private"},
            "ok",
        )
        trace.finish(success=True, final_response="complete")

        capsule = ResearchCapsuleManager(
            workspace,
            source_root=workspace,
            clock=lambda: 24.0,
            id_factory=lambda: "stage24",
        ).capture(
            project_id="project:stage24",
            title="Stage 24",
            objective="Preserve a reproducible result",
            chat_id="webchat:stage24",
            trace=trace,
        )

        assert capsule.file_count == 1
        assert capsule.files[0].path == "result.dat"
        assert capsule.files[0].sha256 == hashlib.sha256(result.read_bytes()).hexdigest()
        assert capsule.skipped_files == [{"path": ".env", "reason": "sensitive_path"}]
        assert capsule.trace["tool_calls"][0]["arguments"] == {
            "command": "simulate API_KEY=[REDACTED]",
            "token": "[REDACTED]",
        }
        manifest = (
            workspace / ".miniclaw" / "research-capsules" /
            "capsule_stage24" / "capsule.json"
        )
        assert manifest.is_file()
        assert "private" not in manifest.read_text(encoding="utf-8")
    print("Stage 24 accepted: research capsules preserve integrity without leaking secrets.")


if __name__ == "__main__":
    main()
