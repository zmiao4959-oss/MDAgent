from miniclaw.agent import Agent, AgentContext
from miniclaw.llm.base import LLMResponse
from miniclaw.memory.session import SessionManager
from miniclaw.tools.registry import tool_registry
import asyncio
import pytest


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        self.calls += 1
        if self.calls <= 2:
            return LLMResponse(
                tool_calls=[
                    {
                        "id": f"call-{self.calls}",
                        "type": "function",
                        "function": {"name": "system_info", "arguments": "{}"},
                    }
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="done")


def test_duplicate_tool_calls_are_only_deduped_within_one_round(tmp_path, monkeypatch):
    agent = Agent(_FakeLLM(), SessionManager(tmp_path / "sessions"))
    context = AgentContext(
        chat_id="webchat:test",
        channel="webchat",
        account_id="tester",
        user_message="run twice",
    )

    calls = []

    async def fake_execute(name, arguments, context=None):
        calls.append((name, dict(arguments)))
        return "ok"

    monkeypatch.setattr(tool_registry, "execute", fake_execute)

    result = __import__("asyncio").run(agent.process_message(context))

    assert result == "done"
    assert calls == [("system_info", {}), ("system_info", {})]


def test_agent_continues_tool_loop_after_stream_fallback(tmp_path, monkeypatch):
    from miniclaw.llm.router import LLMRouter
    from miniclaw.llm.base import LLMStreamChunk
    import httpx

    class Provider:
        def __init__(self):
            self.stream_calls = 0
        async def chat_stream(self, *args, **kwargs):
            self.stream_calls += 1
            if self.stream_calls == 1:
                raise httpx.RemoteProtocolError("incomplete chunked read")
            yield LLMStreamChunk(delta_content="done", finish_reason="stop")
        async def chat(self, *args, **kwargs):
            return LLMResponse(tool_calls=[{"id": "resume", "type": "function", "function": {"name": "execute", "arguments": '{"command":"echo recovered"}'}}], finish_reason="tool_calls")

    router = object.__new__(LLMRouter)
    router.providers = [Provider()]
    agent = Agent(router, SessionManager(tmp_path / "sessions"))
    context = AgentContext(chat_id="webchat:resume", channel="webchat", account_id="tester", user_message="finish the task")
    calls = []
    async def execute(name, arguments, context=None):
        calls.append((name, arguments["command"]))
        return "executed"
    async def stream(text):
        pass
    monkeypatch.setattr(tool_registry, "execute", execute)
    assert asyncio.run(agent.process_message(context, on_stream_chunk=stream)) == "done"
    assert calls == [("execute", "echo recovered")]
    assert router.providers[0].stream_calls == 2


def test_empty_llm_response_is_not_task_completion(tmp_path):
    class Empty:
        async def chat(self, *args, **kwargs):
            return LLMResponse()
    agent = Agent(Empty(), SessionManager(tmp_path / "sessions"))
    context = AgentContext(chat_id="webchat:empty", channel="webchat", account_id="tester", user_message="finish the task")
    with pytest.raises(RuntimeError, match="task is unfinished"):
        asyncio.run(agent.process_message(context))
