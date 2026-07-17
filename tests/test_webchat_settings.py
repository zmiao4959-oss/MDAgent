from pathlib import Path

from miniclaw.channels.webchat import (
    TASK_BEHAVIOR_DEFAULTS,
    collect_runtime_diagnostics,
    gpumd_knowledge_snapshot,
    load_task_behavior,
    normalize_task_behavior,
    save_task_behavior,
)
from miniclaw.config import config


STATIC = Path(__file__).parents[1] / "miniclaw" / "channels" / "web" / "static"


def test_settings_dialog_and_controls_exist():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "btn-settings",
        "settings-dialog",
        "settings-form",
        "setting-theme",
        "setting-message-size",
        "setting-code-wrap",
        "setting-auto-scroll",
        "setting-enter-to-send",
        "setting-show-quick-actions",
        "gpumd-knowledge-status",
        "gpumd-corpus-count",
        "gpumd-index-count",
        "gpumd-model",
        "gpumd-sync-docs",
        "gpumd-update-index",
        "runtime-diagnostic-status",
        "runtime-diagnostic-grid",
        "refresh-runtime-diagnostics",
        "export-runtime-diagnostics",
        "setting-task-concurrency",
        "setting-task-retries",
        "setting-task-notifications",
        "evolution-status",
        "evolution-candidate-count",
        "evolution-verified-count",
        "evolution-rejected-count",
        "evolution-filter",
        "evolution-experience-list",
        "refresh-evolution-experiences",
    ):
        assert f'id="{element_id}"' in html


def test_settings_are_persisted_and_applied_by_frontend():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'miniclaw_webchat_settings_v1' in js
    assert "localStorage.setItem(SETTINGS_STORAGE_KEY" in js
    assert "document.documentElement.dataset.theme" in js
    assert "webchatSettings.autoScroll" in js
    assert "webchatSettings.enterToSend" in js
    assert 'fetch("/api/knowledge/gpumd")' in js
    assert "runGpumdKnowledgeAction(\"sync\")" in js
    assert "runGpumdKnowledgeAction(\"index\")" in js
    assert 'fetch("/api/diagnostics")' in js
    assert 'fetch("/api/settings/task-behavior")' in js
    assert "Notification.requestPermission()" in js
    assert "notifyCompletedProjects" in js
    assert "showTaskCompletionNotification" in js
    assert 'fetch(`/api/evolution/experiences?' in js
    assert 'fetch("/api/evolution/feedback"' in js
    assert "submitEvolutionFeedback" in js
    assert "refreshEvolutionExperiences" in js
    assert "experience.usage_count" in js
    assert "experience.task_pattern" in js


def test_settings_css_has_light_theme_and_accessibility_states():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert 'html[data-theme="light"]' in css
    assert 'html[data-code-wrap="true"]' in css
    assert '.quick-actions[hidden]' in css
    assert '@media (max-width: 560px)' in css
    assert '.knowledge-metrics' in css
    assert '.knowledge-status[data-state="running"]' in css
    assert '.diagnostic-grid' in css
    assert '.diagnostic-dot[data-state="unavailable"]' in css
    assert ".evolution-card" in css
    assert '.evolution-badge[data-status="verified"]' in css
    assert ".evolution-actions" in css


def test_gpumd_knowledge_snapshot_reports_incremental_index_state(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.jsonl"
    index = tmp_path / "index.json"
    corpus.write_text('{"id":"a"}\n{"id":"b"}\n', encoding="utf-8")
    monkeypatch.setattr(config.rag, "model", "test-embedding")
    index.write_text(
        '{"schema_version":2,"model":"test-embedding","count":2,"records":[]}',
        encoding="utf-8",
    )

    snapshot = gpumd_knowledge_snapshot(corpus, index)

    assert snapshot["state"] == "ready"
    assert snapshot["corpus"]["count"] == 2
    assert snapshot["index"]["count"] == 2
    assert "api_key" not in snapshot


def test_gpumd_knowledge_snapshot_detects_model_change(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.jsonl"
    index = tmp_path / "index.json"
    corpus.write_text('{"id":"a"}\n', encoding="utf-8")
    index.write_text(
        '{"schema_version":2,"model":"old-model","count":1,"records":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config.rag, "model", "new-model")

    assert gpumd_knowledge_snapshot(corpus, index)["state"] == "model_mismatch"


def test_task_behavior_is_normalized_and_persisted(tmp_path):
    path = tmp_path / "task-behavior.json"
    settings = normalize_task_behavior({
        "max_concurrency": 99,
        "retry_count": -1,
        "notify_on_completion": True,
    })
    save_task_behavior(path, settings)

    assert load_task_behavior(path) == {
        "max_concurrency": 4,
        "retry_count": 0,
        "notify_on_completion": True,
    }


def test_task_behavior_uses_defaults_for_invalid_file(tmp_path):
    path = tmp_path / "task-behavior.json"
    path.write_text("not json", encoding="utf-8")

    assert load_task_behavior(path) == TASK_BEHAVIOR_DEFAULTS


def test_runtime_diagnostics_reports_expected_capabilities(monkeypatch):
    monkeypatch.setattr("miniclaw.channels.webchat.shutil.which", lambda _name: None)

    diagnostics = collect_runtime_diagnostics()
    items = {item["id"]: item for item in diagnostics["items"]}

    assert {"gpumd", "lammps", "ssh", "gpu", "embedding"} <= set(items)
    assert items["gpumd"]["state"] == "unavailable"
    assert "api_key" not in diagnostics
