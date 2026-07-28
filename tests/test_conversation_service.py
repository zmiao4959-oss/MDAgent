import asyncio

from miniclaw.channels.web.conversation_service import ConversationService
from miniclaw.llm.base import LLMMessage
from miniclaw.memory.session import SessionManager


def test_conversation_service_lifecycle_history_and_exports(tmp_path):
    async def scenario():
        sessions = SessionManager(tmp_path / "sessions")
        service = ConversationService(sessions)
        source = sessions.get_or_create("webchat:source", "webchat", "local")
        source.metadata.update(alias="Research", viz_done=[{"path": "result.gif"}])
        source.add_message(LLMMessage(role="system", content="system"))
        source.add_message(LLMMessage(role="user", content="question"))
        source.add_message(
            LLMMessage(
                role="tool",
                name="calculator",
                content="42",
            )
        )
        await sessions.save(source)

        rows = await service.list()
        assert rows[0]["alias"] == "Research"

        history = await service.history(source.chat_id)
        assert history["messages"] == [
            {"role": "user", "content": "question"},
            {"role": "tool", "content": "[calculator] 42"},
        ]
        assert history["viz_done"] == [{"path": "result.gif"}]

        forked = await service.fork(source.chat_id, 2)
        fork_session = await sessions.resolve_session(forked["chat_id"])
        assert forked["message_count"] == 2
        assert fork_session.metadata["forked_from"] == source.chat_id
        assert fork_session.metadata["total_tokens"] == 0

        renamed = await service.rename(forked["chat_id"], "Fork")
        assert renamed["alias"] == "Fork"

        exported = await service.export_json(source.chat_id)
        markdown = await service.export_markdown(source.chat_id)
        assert len(exported["messages"]) == 3
        assert "# MiniClaw 对话导出" in markdown
        assert "calculator" in markdown

        deleted = await service.delete(forked["chat_id"])
        assert deleted["ok"] is True
        assert await sessions.resolve_session(forked["chat_id"]) is None

    asyncio.run(scenario())
