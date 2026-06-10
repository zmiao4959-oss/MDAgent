"""
channels/telegram.py — Telegram Bot Adapter
"""
import asyncio
from ..agent import AgentContext
from ..logger import get_logger
from .base import BaseChannelAdapter

logger = get_logger(__name__)


class TelegramAdapter(BaseChannelAdapter):
    def __init__(self, bot_token: str, allowed_users: list = None):
        super().__init__("telegram")
        self.bot_token = bot_token
        self.allowed_users = set(allowed_users or [])
        self.bot = None
        self._stop_event: asyncio.Event | None = None

    async def start(self):
        """启动 Telegram bot 轮询（与外部 asyncio 事件循环协作，不阻塞 run_polling）"""
        try:
            from telegram import Update
            from telegram.ext import Application, ContextTypes, MessageHandler, filters
        except ImportError:
            logger.error("python-telegram-bot not installed")
            return

        app = Application.builder().token(self.bot_token).build()
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        
        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text:
                return
            
            user_id = str(update.effective_user.id)
            if self.allowed_users and user_id not in self.allowed_users:
                await update.message.reply_text("Access denied.")
                return
            
            chat_id = f"telegram:{update.effective_chat.id}"
            
            # 发送 typing 指示器
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            
            # 构建统一上下文
            ctx = AgentContext(
                chat_id=chat_id,
                channel="telegram",
                account_id=user_id,
                user_message=update.message.text,
                metadata={
                    "telegram_user_id": user_id,
                    "telegram_chat_id": update.effective_chat.id,
                },
            )
            
            if self._message_handler:
                response = await self._message_handler(ctx)
                if response:
                    await update.message.reply_text(response)
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info(f"Telegram adapter started for {len(self.allowed_users)} allowed users")
        try:
            async with app:
                # 与 Application.run_polling() 相同顺序，见 PTB 文档
                await app.updater.start_polling()
                await app.start()
                try:
                    await stop_event.wait()
                finally:
                    await app.updater.stop()
                    if app.running:
                        await app.stop()
        finally:
            self._stop_event = None

    async def stop(self):
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()
    
    async def send_message(self, chat_id: str, text: str, **kwargs):
        # Telegram 消息在处理函数内部直接回复，这里留作主动推送备用
        pass