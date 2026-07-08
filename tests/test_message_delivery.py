import asyncio

from miniclaw.channels.webchat import WebChatAdapter
from miniclaw.cron_scheduler import CronJob, CronScheduler
from miniclaw.memory.session import SessionManager
from miniclaw.tools.message_tool import (
    register_send_callback,
    send_message_tool,
    unregister_send_callback,
)


class _Agent:
    async def process_message(self, _context):
        return "scheduled result"


def test_webchat_proactive_message_is_saved_to_target_session(tmp_path):
    sessions = SessionManager(tmp_path / "sessions")
    adapter = WebChatAdapter(session_manager=sessions)

    asyncio.run(adapter.send_message("default", "hello from cron"))
    restored = asyncio.run(sessions.resolve_session("webchat:default"))

    assert restored is not None
    assert restored.messages[-1].role == "assistant"
    assert restored.messages[-1].content == "hello from cron"


def test_cron_delivers_completed_response(monkeypatch):
    scheduler = CronScheduler(_Agent())
    job = CronJob(
        "summary", "0 8 * * *", "summarize",
        delivery_channel="webchat", delivery_to="default",
    )
    delivered = {}

    async def send_message(**kwargs):
        delivered.update(kwargs)
        return "Message sent"

    monkeypatch.setattr("miniclaw.cron_scheduler.send_message_tool", send_message)
    asyncio.run(scheduler._fire_job(job))

    assert delivered == {
        "message": "scheduled result",
        "channel": "webchat",
        "chat_id": "default",
    }


def test_send_message_uses_current_context_by_default():
    delivered = {}

    async def callback(chat_id, message):
        delivered["chat_id"] = chat_id
        delivered["message"] = message

    register_send_callback("webchat", callback)
    try:
        result = asyncio.run(
            send_message_tool(
                "hello",
                _context={"channel": "webchat", "chat_id": "webchat:ctx"},
            )
        )
    finally:
        unregister_send_callback("webchat")

    assert "default" not in result
    assert "webchat:webchat:ctx" in result
    assert delivered == {"chat_id": "webchat:ctx", "message": "hello"}
