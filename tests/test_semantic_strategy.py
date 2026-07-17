import sqlite3

from miniclaw.learning.experience import ExperienceEngine, ExperienceStore
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.strategy import build_strategy
from miniclaw.learning.trace import EvolutionTrace


def _trace(path, command="python validate.py", *, secret=""):
    trace = EvolutionTrace(chat_id="semantic", objective="validate deployment configuration")
    trace.add_tool("read", {"path": str(path), "api_key": secret}, "configuration loaded")
    trace.add_tool("execute", {"command": command}, "completed")
    trace.finish(success=True, final_response="validated")
    return trace


def test_semantic_steps_capture_resource_and_command_family_without_raw_values(tmp_path):
    trace = _trace(tmp_path / "private" / "config.yaml", secret="super-secret")

    strategy = build_strategy(trace, "deployment validate")
    rendered = str(strategy.to_dict())

    assert strategy.steps[0].action == "read"
    assert strategy.steps[0].resource_type == "yaml-config"
    assert strategy.steps[1].action == "execute"
    assert strategy.steps[1].command_family == "python"
    assert "super-secret" not in rendered
    assert str(tmp_path) not in rendered
    assert "validate.py" not in rendered


def test_same_tool_order_with_different_semantics_does_not_merge(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)

    yaml_experience = engine.observe_trace(_trace(tmp_path / "config.yaml", "python check.py"))
    lammps_experience = engine.observe_trace(_trace(tmp_path / "model.in", "lmp -in model.in"))

    assert yaml_experience is not None and lammps_experience is not None
    assert yaml_experience.experience_id != lammps_experience.experience_id
    assert len(store.all()) == 2


def test_structured_strategy_is_injected_with_conditions_validation_and_fallback(tmp_path):
    service = EvolutionService(tmp_path)
    for _ in range(3):
        experience = service.complete_trace(_trace(tmp_path / "config.yaml"))
    assert experience is not None and experience.status == "verified"

    prompt = service.prompt_prefix("validate deployment configuration")

    assert "Conditions:" in prompt
    assert "Workflow:" in prompt
    assert "Validation:" in prompt
    assert "Fallback:" in prompt
    assert "yaml-config" in prompt


def test_legacy_database_adds_empty_strategy_column(tmp_path):
    path = tmp_path / "legacy.db"
    ExperienceStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE experiences RENAME TO experiences_new")
        connection.execute(
            """
            CREATE TABLE experiences AS SELECT
                experience_id, fingerprint, situation, lesson, scope_json,
                failure_pattern, status, positive_evidence, negative_evidence,
                observations, confidence, created_at, updated_at, last_used_at,
                usage_count, task_pattern FROM experiences_new
            """
        )
        connection.execute("DROP TABLE experiences_new")

    migrated = ExperienceStore(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiences)")}
    assert "strategy_json" in columns
    assert migrated.all() == []
