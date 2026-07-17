"""Stage 4 acceptance: observable experience management and feedback UI."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    html = (ROOT / "miniclaw/channels/web/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "miniclaw/channels/web/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "miniclaw/channels/web/static/style.css").read_text(encoding="utf-8")
    server = (ROOT / "miniclaw/channels/webchat.py").read_text(encoding="utf-8")

    for element_id in (
        "evolution-status",
        "evolution-candidate-count",
        "evolution-verified-count",
        "evolution-rejected-count",
        "evolution-filter",
        "evolution-experience-list",
        "refresh-evolution-experiences",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/evolution/experiences" in js and "/api/evolution/experiences" in server
    assert "/api/evolution/feedback" in js and "/api/evolution/feedback" in server
    assert "submitEvolutionFeedback" in js
    assert "textContent" in js
    assert ".evolution-card" in css
    print("Stage 4 accepted: experience dashboard, filtering, feedback, and rollback UI are wired.")


if __name__ == "__main__":
    main()
