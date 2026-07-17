"""Stage 21 acceptance: evolution web concerns live in dedicated modules."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    webchat = (ROOT / "miniclaw/channels/webchat.py").read_text(encoding="utf-8")
    routes = (ROOT / "miniclaw/channels/web/evolution_routes.py").read_text(encoding="utf-8")
    app = (ROOT / "miniclaw/channels/web/static/app.js").read_text(encoding="utf-8")
    evolution = (ROOT / "miniclaw/channels/web/static/evolution.js").read_text(encoding="utf-8")

    route_paths = set(re.findall(r'@app\.(?:get|post)\("([^"]+)"\)', routes))
    required = {
        "/api/evolution/experiences",
        "/api/evolution/feedback",
        "/api/evolution/governance",
        "/api/evolution/skill-drafts",
        "/api/evolution/executable-policies",
        "/api/evolution/source/proposals",
        "/api/evolution/source/experiments",
    }
    assert required <= route_paths
    assert "register_evolution_routes(app, self)" in webchat
    assert '@app.get("/api/evolution/' not in webchat
    assert len(webchat.splitlines()) < 1200
    assert "createEvolutionPanel" in app
    assert 'fetch("/api/evolution/feedback"' not in app
    assert 'fetch("/api/evolution/feedback"' in evolution
    assert len(app.splitlines()) < 2300

    subprocess.run(
        ["node", "--check", "miniclaw/channels/web/static/app.js"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["node", "--check", "miniclaw/channels/web/static/evolution.js"],
        cwd=ROOT,
        check=True,
    )
    print("Stage 21 accepted: evolution routes and UI are isolated modules.")


if __name__ == "__main__":
    main()
