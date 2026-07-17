from miniclaw.learning.trace import EvolutionTrace, TraceStore


def test_trace_round_trip_and_quality_score(tmp_path):
    trace = EvolutionTrace(chat_id="chat-1", objective="run a simulation")
    trace.add_tool("read", {"path": "input.in"}, "contents")
    trace.finish(
        success=True,
        final_response="Simulation completed",
        rounds=2,
        prompt_tokens=100,
        completion_tokens=20,
    )

    store = TraceStore(tmp_path / "traces.jsonl")
    store.append(trace)
    loaded = store.recent()

    assert len(loaded) == 1
    assert loaded[0].run_id == trace.run_id
    assert loaded[0].tool_calls[0].name == "read"
    assert loaded[0].score == 1.0


def test_trace_records_tool_failures_and_penalizes_score(tmp_path):
    trace = EvolutionTrace(chat_id="chat-2", objective="broken task")
    trace.add_tool("execute", {"command": "bad"}, "Error: command failed")
    trace.finish(success=False, error="agent loop failed", hit_max_rounds=True)

    assert trace.tool_calls[0].success is False
    assert len(trace.errors) == 2
    assert trace.score == 0.0

    path = tmp_path / "traces.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    store = TraceStore(path)
    store.append(trace)
    assert [item.run_id for item in store.recent()] == [trace.run_id]
