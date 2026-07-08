from miniclaw.agent import Agent, AgentContext
from miniclaw.llm.base import LLMResponse
from miniclaw.memory.session import SessionManager
from miniclaw.tools.registry import tool_registry


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
