import sqlite3

from miniclaw.learning.experience import ExperienceEngine, ExperienceStore
from miniclaw.learning.trace import EvolutionTrace


def _successful_trace() -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="c", objective="read and run input")
    trace.add_tool("read", {"path": "input.in"}, "ok")
    trace.add_tool("execute", {"command": "run"}, "completed")
    trace.finish(success=True, final_response="done")
    return trace


def test_repeated_success_promotes_candidate(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)

    for _ in range(3):
        experience = engine.observe_trace(_successful_trace())

    assert experience is not None
    assert experience.status == "pending_evaluation"
    assert experience.positive_evidence == 3
    assert store.verified_for("read then execute input") == []


def test_negative_evidence_rejects_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)
    experience = engine.observe_trace(_successful_trace())
    assert experience is not None

    store.record_feedback(experience.experience_id, positive=False)
    rejected = store.record_feedback(experience.experience_id, positive=False)

    assert rejected is not None
    assert rejected.status == "rejected"
    assert store.verified_for("read execute") == []


def test_positive_feedback_is_persisted_even_before_promotion(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)
    experience = engine.observe_trace(_successful_trace())
    assert experience is not None

    updated = store.record_feedback(experience.experience_id, positive=True)

    assert updated is not None
    assert updated.positive_evidence == experience.positive_evidence + 1
    assert updated.status == "candidate"
    persisted = next(item for item in store.all() if item.experience_id == experience.experience_id)
    assert persisted.positive_evidence == updated.positive_evidence


def test_failure_creates_unverified_candidate(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    trace = EvolutionTrace(chat_id="c", objective="run task")
    trace.add_tool("execute", {"command": "bad"}, "Error: exit code 17")
    trace.finish(success=False, error="Error: exit code 17")

    experience = ExperienceEngine(store).observe_trace(trace)

    assert experience is not None
    assert experience.status == "candidate"
    assert experience.positive_evidence == 0
    assert "#" in experience.failure_pattern


def test_existing_database_is_migrated_with_usage_count(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE experiences (
                experience_id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                situation TEXT NOT NULL, lesson TEXT NOT NULL, scope_json TEXT NOT NULL,
                failure_pattern TEXT NOT NULL, status TEXT NOT NULL,
                positive_evidence INTEGER NOT NULL, negative_evidence INTEGER NOT NULL,
                observations INTEGER NOT NULL, confidence REAL NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                last_used_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO experiences VALUES (
                'legacy', 'legacy', 'generic strategy', 'generic lesson', '[]', '',
                'verified', 3, 0, 3, 0.8, 1.0, 1.0, 1.0
            )
            """
        )

    ExperienceStore(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiences)")}
        legacy_status = connection.execute(
            "SELECT status FROM experiences WHERE experience_id = 'legacy'"
        ).fetchone()[0]
    assert {"usage_count", "task_pattern"} <= columns
    assert legacy_status == "candidate"


def test_same_tools_in_different_task_contexts_do_not_merge(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)

    def observe(objective: str):
        trace = EvolutionTrace(chat_id="c", objective=objective)
        trace.add_tool("read", {"path": "input"}, "ok")
        trace.add_tool("execute", {"command": "run"}, "completed")
        trace.finish(success=True, final_response="done")
        return engine.observe_trace(trace)

    for _ in range(3):
        lammps = observe("prepare a LAMMPS molecular dynamics simulation")
        web = observe("research browser accessibility documentation")

    assert lammps is not None and web is not None
    assert lammps.experience_id != web.experience_id
    assert lammps.status == web.status == "pending_evaluation"
    assert len(store.all()) == 2
