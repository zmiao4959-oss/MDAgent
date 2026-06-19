"""
main.py — 完整版启动入口（所有阶段集成）
"""
import asyncio
import signal
from typing import Optional

from miniclaw.config import config, WORKSPACE_DIR
from miniclaw.logger import get_logger
from miniclaw.llm.router import LLMRouter
from miniclaw.memory.session import SessionManager
from miniclaw.agent import Agent
from miniclaw.heartbeat import HeartbeatLoop
from miniclaw.cron_scheduler import CronScheduler, CronJob
from miniclaw.subagent import SubAgentManager
from miniclaw.gateway.server import GatewayServer
from miniclaw.channels.webchat import WebChatAdapter
from miniclaw.tools import ensure_tools_loaded
from miniclaw.tools.message_tool import register_send_callback, unregister_send_callback

ensure_tools_loaded()

logger = get_logger("miniclaw")

_SHUTDOWN_TIMEOUT_SEC = 15.0


async def _await_task(
    task: Optional[asyncio.Task],
    *,
    name: str,
    timeout: float = _SHUTDOWN_TIMEOUT_SEC,
) -> None:
    """等待后台 task 结束；超时则 cancel。"""
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("%s did not stop within %.0fs, cancelling", name, timeout)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("%s raised during shutdown: %s", name, e)


async def _cancel_task(task: Optional[asyncio.Task], *, name: str) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("%s raised during cancel: %s", name, e)


async def main():
    logger.info("=== MiniClaw Starting ===")

    router = LLMRouter(config.llm, config.llm.fallback_providers)
    session_mgr = SessionManager(WORKSPACE_DIR / "sessions")
    agent = Agent(router, session_mgr)
    subagent_mgr = SubAgentManager(agent, session_mgr)

    gateway = GatewayServer(agent)
    gateway_task = asyncio.create_task(gateway.start(), name="gateway")

    adapters = []

    if config.channels.enabled.get("webchat", True):
        webchat = WebChatAdapter(host="127.0.0.1", port=8000, session_manager=session_mgr)
        webchat.set_message_handler(agent.process_message)
        adapters.append(webchat)

    if config.channels.enabled.get("telegram", False):
        tg_token = config.channels.settings.get("telegram", {}).get("bot_token", "")
        if tg_token:
            from miniclaw.channels.telegram import TelegramAdapter
            tg = TelegramAdapter(tg_token)
            tg.set_message_handler(agent.process_message)
            adapters.append(tg)

    for adapter in adapters:
        register_send_callback(adapter.channel_name, adapter.send_message)

    channel_tasks = [
        asyncio.create_task(a.start(), name=f"channel-{a.channel_name}")
        for a in adapters
    ]

    heartbeat = HeartbeatLoop(agent)
    heartbeat_task = asyncio.create_task(heartbeat.run(), name="heartbeat")

    cron = CronScheduler(agent)
    cron.add_job(CronJob(
        name="morning_summary",
        cron_expr="0 8 * * *",
        task_prompt="Check today's calendar, weather, and any important emails. "
                     "Write a brief morning summary.",
        delivery_channel="webchat",
        delivery_to="default",
    ))
    cron_task = asyncio.create_task(cron.run(), name="cron")

    stop_event = asyncio.Event()

    def shutdown(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info(f"Gateway: ws://{config.gateway.host}:{config.gateway.port}")
    logger.info(f"WebChat: http://127.0.0.1:8000")
    logger.info(f"Heartbeat: every {config.agent.heartbeat_interval_min} min")
    logger.info(f"Cron jobs: {len(cron.jobs)} registered")
    logger.info("=== MiniClaw Ready ===")

    await stop_event.wait()
    logger.info("Shutting down MiniClaw...")

    # 1. 先停调度类后台任务，避免退出过程中再触发 Agent
    await _cancel_task(heartbeat_task, name="heartbeat")
    await _cancel_task(cron_task, name="cron")

    # 2. 通知各服务优雅退出
    for adapter in adapters:
        await adapter.stop()
        unregister_send_callback(adapter.channel_name)
    await gateway.stop()

    # 3. 等待 WebChat / Gateway 等 server task 真正结束
    for task in channel_tasks:
        await _await_task(task, name=task.get_name())
    await _await_task(gateway_task, name="gateway")

    logger.info("=== MiniClaw Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
