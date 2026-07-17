from pathlib import Path

from miniclaw.config import Config, LLMConfig, resolve_config_path


def test_explicit_config_path_has_priority(tmp_path: Path):
    explicit = tmp_path / "custom.yaml"
    assert resolve_config_path(tmp_path / "workspace", environ={"MINICLAW_CONFIG": str(explicit)}) == explicit


def test_user_config_wins_when_present(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_config = tmp_path / "config.yaml"
    user_config.write_text("llm: {}", encoding="utf-8")

    assert resolve_config_path(workspace, environ={}) == user_config


def test_falls_back_to_packaged_example_for_fresh_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert resolve_config_path(workspace, environ={}).name == "config.yaml"
    assert resolve_config_path(workspace, environ={}).parent.name == "miniclaw"


def test_provider_specific_api_key_beats_shared_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")
    monkeypatch.setenv("MINICLAW_API_KEY", "shared-key")

    assert LLMConfig(provider="openai").resolved_api_key == "provider-key"


def test_evolution_config_is_bounded(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("evolution:\n  max_injected: 99\n", encoding="utf-8")

    loaded = Config(path)

    assert loaded.evolution.enabled is True
    assert loaded.evolution.max_injected == 10
    assert loaded.evolution.auto_evaluate_applied is True
