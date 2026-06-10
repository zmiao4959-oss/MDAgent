"""
cron_scheduler.py — Cron 定时任务调度器
"""
import asyncio
from croniter import croniter
from datetime import datetime
from typing import Dict, Callable, Any
from .agent import Agent, AgentContext
from .logger import get_logger

logger = get_logger(__name__)


class CronJob:
    def __init__(
        self,
        name: str,
        cron_expr: str,        # "0 9 * * *" (每天 9:00)
        task_prompt: str,      # 给 Agent 的提示
        chat_id: str = "cron:system",
        channel: str = "cron",
        delivery_channel: str = None,  # 结果发送到哪个 channel
        delivery_to: str = None,       # 结果发送到哪个 chat_id
    ):
        self.name = name
        self.cron_expr = cron_expr
        self.task_prompt = task_prompt
        self.chat_id = chat_id
        self.channel = channel
        self.delivery_channel = delivery_channel
        self.delivery_to = delivery_to


class CronScheduler:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.jobs: list[CronJob] = []
        self._task: asyncio.Task = None
    
    def add_job(self, job: CronJob):
        self.jobs.append(job)
        logger.info(f"Cron job added: {job.name} ({job.cron_expr})")
    
    async def run(self):
        """持续运行 cron 循环（每分钟检查一次）"""
        logger.info(f"Cron scheduler started with {len(self.jobs)} jobs")
        while True:
            now = datetime.now()
            for job in self.jobs:
                cron = croniter(job.cron_expr, now)
                prev_fire = cron.get_prev(datetime)
                
                # 如果上次触发时间距离现在不到 60 秒，触发
                if (now - prev_fire).total_seconds() < 60:
                    await self._fire_job(job)
            
            await asyncio.sleep(60)
    
    async def _fire_job(self, job: CronJob):
        logger.info(f"Cron firing: {job.name}")
        ctx = AgentContext(
            chat_id=job.chat_id,
            channel=job.channel,
            account_id="cron",
            user_message=f"[Scheduled Task: {job.name}]\n{job.task_prompt}",
        )
        response = await self.agent.process_message(ctx)
        
        # 如果指定了投递目标，主动发送结果
        if response and job.delivery_channel:
            logger.info(f"Cron '{job.name}' completed: {response[:100]}...")
            # 在实际实现中，这里会调用 channel adapter 发送消息