"""
heartbeat.py — 心跳系统
"""
import asyncio
from pathlib import Path
from .settings import config, WORKSPACE_DIR
from .agent import Agent, AgentContext
from .logger import get_logger

logger = get_logger(__name__)


class HeartbeatLoop:
    def __init__(self, agent: Agent, check_session_id: str = "heartbeat:system"):
        self.agent = agent
        self.check_session_id = check_session_id
        self.heartbeat_file = WORKSPACE_DIR / "HEARTBEAT.md"
    
    async def run(self):
        """持续运行心跳循环"""
        interval = config.agent.heartbeat_interval_min * 60  # 转为秒
        logger.info(f"Heartbeat loop started (every {config.agent.heartbeat_interval_min} min)")
        
        while True:
            await asyncio.sleep(interval)
            try:
                await self._do_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    async def _do_heartbeat(self):
        """执行一次心跳检查"""
        # 读取 HEARTBEAT.md 中的任务列表
        tasks = ""
        if self.heartbeat_file.exists():
            content = self.heartbeat_file.read_text(encoding="utf-8")
            # 只读取非注释行
            tasks = "\n".join(
                line for line in content.split("\n")
                if line.strip() and not line.strip().startswith("#")
            )
        
        if not tasks:
            return  # 文件为空，跳过
        
        # 构建心跳消息
        heartbeat_msg = (
            "This is a heartbeat poll. Check the following tasks and take action if needed:\n"
            f"{tasks}\n\n"
            "If nothing needs attention, reply exactly: HEARTBEAT_OK\n"
            "If something needs attention, act on it and report."
        )
        
        ctx = AgentContext(
            chat_id=self.check_session_id,
            channel="heartbeat",
            account_id="system",
            user_message=heartbeat_msg,
        )
        
        response = await self.agent.process_message(ctx)
        
        # 如果回复包含 HEARTBEAT_OK，什么都不要做
        if response and "HEARTBEAT_OK" in response:
            logger.debug("Heartbeat: nothing to do")
        elif response:
            logger.info(f"Heartbeat: agent has something to report: {response[:100]}...")
