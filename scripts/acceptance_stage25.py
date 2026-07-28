"""Stage 25 acceptance: verify, redact, export, and surface research capsules."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miniclaw.research_capsule import CapsuleIntegrityError, ResearchCapsuleManager


def main() -> None:
    with TemporaryDirectory() as directory:
        workspace = Path(directory) / "project"
        workspace.mkdir()
        input_file = workspace / "experiment.in"
        source = "steps=1000\nAPI_KEY=stage25-private\n"
        input_file.write_text(source, encoding="utf-8")
        manager = ResearchCapsuleManager(
            workspace,
            source_root=workspace,
            clock=lambda: 25.0,
            id_factory=lambda: "stage25",
        )
        capsule = manager.capture(
            project_id="project:stage25",
            title="Stage 25",
            objective="Verify and export safely",
            chat_id="webchat:stage25",
        )

        assert manager.verify(capsule.capsule_id).status == "verified"
        input_file.write_text("steps=1001\n", encoding="utf-8")
        assert manager.verify(capsule.capsule_id).status == "changed"
        try:
            manager.export_capsule(
                capsule.capsule_id,
                confirmation=f"EXPORT {capsule.capsule_id}",
            )
        except CapsuleIntegrityError:
            pass
        else:
            raise AssertionError("changed files must block a full export")

        input_file.write_text(source, encoding="utf-8")
        exported = manager.export_capsule(
            capsule.capsule_id,
            confirmation=f"EXPORT {capsule.capsule_id}",
        )
        with zipfile.ZipFile(exported.path) as archive:
            assert "stage25-private" not in archive.read("files/experiment.in").decode("utf-8")
            metadata = json.loads(archive.read("export.json"))
            assert metadata["integrity_status"] == "verified"
            assert metadata["redacted_file_count"] == 1

        index = ROOT / "miniclaw" / "channels" / "web" / "static" / "index.html"
        assert 'id="tab-capsules"' in index.read_text(encoding="utf-8")
    print("Stage 25 accepted: capsules are verified and exported without leaking text secrets.")


if __name__ == "__main__":
    main()
