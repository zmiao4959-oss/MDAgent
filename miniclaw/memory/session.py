"""
memory/session.py — 会话管理（消息持久化 + 上下文压缩）
"""
import json
import time
from pathlib import Path
from typing import List, Dict, Optional, AsyncIterator
from dataclasses import dataclass, field
from ..llm.base import LLMMessage
from ..llm.router import LLMRouter
from ..settings import config
from ..logger import get_logger

logger = get_logger(__name__)

@dataclass
class Session:
    """一个会话实例"""
    session_id: str
    chat_id: str                    # 外部 channel 的 chat_id
    messages: List[LLMMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)  # channel, account_id 等

    def add_message(self, msg: LLMMessage):
        self.messages.append(msg)
        self.last_active = time.time()
    
    def estimate_tokens(self) -> int:
        """使用 tiktoken 精确估算 token 数（fallback: 字符估算）。"""
        try:
            from ..llm.token_counter import count_messages_tokens
            return count_messages_tokens(self.messages)
        except Exception:
            return sum(len(m.content or "") // 4 for m in self.messages)
    
    def should_compact(self) -> bool:
        """是否需要压缩上下文"""
        return self.estimate_tokens() > config.agent.max_context_tokens * 0.8
    
    def to_save_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "chat_id": self.chat_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                    "tool_calls": m.tool_calls,
                    "name": m.name,
                    "reasoning_content": m.reasoning_content,
                }
                for m in self.messages
            ],
            "created_at": self.created_at,
            "last_active": self.last_active,
            "metadata": self.metadata,
        }
    # @classmethod 相当于把实例化之后再调用的方法包装到类中，而不是实例里面
    # from_save_dict 的目的就是从“以前保存的数据”中重新创建一个全新的 Session 对象（唤醒），而不是在现有的某个 Session 上追加消息（修改）
    @classmethod
    def from_save_dict(cls, data: dict) -> "Session":
        s = cls(
            session_id=data["session_id"],
            chat_id=data["chat_id"],
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            last_active=data.get("last_active", time.time()),
        )
        for md in data.get("messages", []):
            s.messages.append(LLMMessage(
                role=md["role"],
                content=md["content"],
                tool_call_id=md.get("tool_call_id"),
                tool_calls=md.get("tool_calls"),
                name=md.get("name"),
                reasoning_content=md.get("reasoning_content"),
            ))
        return s


class SessionManager:
    """会话管理器（内存缓存 + SQLite 持久化 + JSON 向后兼容）"""

    def __init__(self, save_dir: Path):
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, Session] = {}  # session_id → Session
        self._chat_to_session: Dict[str, str] = {}  # chat_id → session_id
        self._store = None  # SessionStore 懒加载

    def _get_store(self):
        """懒加载 SQLite SessionStore。"""
        if self._store is None:
            from .session_store import get_store
            self._store = get_store()
        return self._store
    
    def get_or_create(self, chat_id: str, channel: str = "unknown", 
                      account_id: str = "") -> Session:
        """根据 chat_id 获取或创建会话"""
        if chat_id in self._chat_to_session:
            sid = self._chat_to_session[chat_id]
            if sid in self._sessions:
                return self._sessions[sid]
        
        # 创建新会话
        sid = f"{channel}:{chat_id}:{int(time.time())}"
        session = Session(
            session_id=sid,
            chat_id=chat_id,
            metadata={"channel": channel, "account_id": account_id},
        )
        self._sessions[sid] = session
        self._chat_to_session[chat_id] = sid
        return session
    
    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)
    
    async def save(self, session: Session):
        # ── 始终写 JSON：人类可读，方便溯源 agent 行为 ──
        path = self.save_dir / f"{session.session_id.replace(':', '_')}.json"
        path.write_text(
            json.dumps(session.to_save_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("Session saved → %s (%s messages)", path.name, len(session.messages))
        # ── 同步写 SQLite：加速查询/列表/搜索 ──
        try:
            store = self._get_store()
            store.save(
                session.session_id,
                session.chat_id,
                channel=session.metadata.get("channel", ""),
                account_id=session.metadata.get("account_id", ""),
                messages=session.to_save_dict().get("messages", []),
                metadata=session.metadata,
                created_at=session.created_at,
                last_active=session.last_active,
            )
        except Exception:
            logger.debug("SQLite save skipped for %s (JSON already written)", session.session_id)
    
    async def load(self, session_id: str) -> Optional[Session]:
        """按 session_id 从 JSON 或 SQLite 加载会话, 并加入内存缓存。"""
        path = self.save_dir / f"{session_id.replace(':', '_')}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            session = Session.from_save_dict(data)
            self._sessions[session_id] = session
            self._chat_to_session[session.chat_id] = session_id
            return session
        return None

    def get_session_by_chat_id(self, chat_id: str) -> Optional[Session]:
        """chat_id 已在内存中映射时返回当前 Session。"""
        sid = self._chat_to_session.get(chat_id)
        if sid and sid in self._sessions:
            return self._sessions[sid]
        return None

    async def load_latest_session_for_chat(self, chat_id: str) -> Optional[Session]:
        """按 chat_id 加载最近活跃会话 — JSON 优先（人类可读主数据源），SQLite 辅助加速。"""
        # 1. 先扫描 JSON 目录（主数据源，人类可读）
        best_la = -1.0
        best_sid: Optional[str] = None
        for path in self.save_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("chat_id") != chat_id:
                continue
            sid = data.get("session_id")
            if not sid:
                continue
            la = float(data.get("last_active", 0))
            if la > best_la:
                best_la = la
                best_sid = sid
        if best_sid:
            return await self.load(best_sid)

        # 2. 回退 SQLite（可能 JSON 文件被手动删除但 DB 里还有）
        try:
            store = self._get_store()
            row = store.resolve_by_chat(chat_id)
            if row:
                return self._session_from_dict(row)
        except Exception:
            pass

        return None

    def _session_from_dict(self, data: dict) -> Session:
        """从 dict（SQLite 或 JSON）构建 Session 对象并加入内存缓存。"""
        sid = data["session_id"]
        s = Session(
            session_id=sid,
            chat_id=data["chat_id"],
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            last_active=data.get("last_active", time.time()),
        )
        for md in data.get("messages", []):
            s.messages.append(LLMMessage(
                role=md["role"],
                content=md["content"],
                tool_call_id=md.get("tool_call_id"),
                tool_calls=md.get("tool_calls"),
                name=md.get("name"),
                reasoning_content=md.get("reasoning_content"),
            ))
        self._sessions[sid] = s
        self._chat_to_session[data["chat_id"]] = sid
        return s

    async def resolve_session(self, chat_id: str) -> Optional[Session]:
        """优先内存，否则从磁盘恢复该 chat_id 的会话。"""
        s = self.get_session_by_chat_id(chat_id)
        if s:
            return s
        return await self.load_latest_session_for_chat(chat_id)

    async def delete_session(self, chat_id: str) -> bool:
        """删除指定 chat_id 的会话（内存 + SQLite + JSON）。"""
        sid = self._chat_to_session.pop(chat_id, None)
        if sid:
            self._sessions.pop(sid, None)
            path = self.save_dir / f"{sid.replace(':', '_')}.json"
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
        # SQLite 删除
        try:
            self._get_store().delete(chat_id)
        except Exception:
            pass
        # 清理遗留 JSON 文件
        for p in self.save_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("chat_id") == chat_id:
                try:
                    p.unlink()
                except OSError:
                    pass
        return True

    def list_conversations_by_channel(self, channel: str) -> List[Dict]:
        """列出某频道全部对话 — 优先 SQLite，回退 JSON 扫描。"""
        # 尝试 SQLite
        try:
            return self._get_store().list_by_channel(channel)
        except Exception:
            pass

        # 回退：JSON 目录扫描
        by_chat: Dict[str, Dict] = {}

        def preview_from_dict(messages: list) -> str:
            for md in reversed(messages):
                if md.get("role") == "user" and (md.get("content") or "").strip():
                    return ((md.get("content") or "")[:80]).replace("\n", " ")
            return "(空对话)"

        for path in self.save_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("metadata", {}).get("channel") != channel:
                continue
            cid = data.get("chat_id")
            if not cid:
                continue
            la = float(data.get("last_active", 0))
            row = {
                "chat_id": cid,
                "session_id": data["session_id"],
                "last_active": la,
                "preview": preview_from_dict(data.get("messages", [])),
                "message_count": len(data.get("messages", [])),
                "total_tokens": data.get("metadata", {}).get("total_tokens", 0),
            }
            by_chat[cid] = row

        for sess in self._sessions.values():
            if sess.metadata.get("channel") != channel:
                continue
            cid = sess.chat_id
            prev = "(空对话)"
            for m in reversed(sess.messages):
                if m.role == "user" and (m.content or "").strip():
                    prev = (m.content or "")[:80].replace("\n", " ")
                    break
            row = {
                "chat_id": cid,
                "session_id": sess.session_id,
                "last_active": float(sess.last_active),
                "preview": prev,
                "message_count": len(sess.messages),
                "total_tokens": sess.metadata.get("total_tokens", 0),
            }
            old = by_chat.get(cid)
            if not old or row["last_active"] >= old["last_active"]:
                by_chat[cid] = row

        return sorted(by_chat.values(), key=lambda x: -float(x["last_active"]))

    def close(self) -> None:
        """关闭 SQLite 存储连接。"""
        if self._store is not None:
            from .session_store import close_store
            close_store()
            self._store = None

    async def compact(self, session: Session, llm_router: LLMRouter) -> None:
        """
        多层上下文压缩（inspired by Claude Code's 5-layer compaction pipeline）:
          Layer 1 — Budget Reduction: 每条消息截断到合理长度
          Layer 2 — Snip: 裁剪旧历史（非 LLM，最便宜）
          Layer 3 — Full Compact: LLM 总结（最贵，最后手段）

        只在 token 估算超过阈值时才触发，逐步升级。
        """
        if len(session.messages) <= config.agent.compaction_keep_messages:
            return

        keep = config.agent.compaction_keep_messages
        threshold = config.agent.max_context_tokens

        # ── Layer 1: Budget Reduction ──
        # 对每条旧消息做长度裁剪，避免单条消息占据过多 token
        budget_cut = 0
        for i, m in enumerate(session.messages[:-keep]):
            if m.role == "tool":
                max_len = 2000  # 工具结果最多保留 2000 字符
            elif m.role == "assistant":
                max_len = 4000  # assistant 消息最多 4000 字符
            else:
                max_len = 2000  # user 消息最多 2000 字符
            if len(m.content or "") > max_len:
                truncated = (m.content or "")[:max_len] + f"\n...[truncated {len(m.content) - max_len} chars]..."
                session.messages[i] = LLMMessage(
                    role=m.role,
                    content=truncated,
                    tool_call_id=m.tool_call_id,
                    tool_calls=m.tool_calls,
                    name=m.name,
                )
                budget_cut += 1

        if budget_cut:
            logger.debug("Layer 1 Budget Reduction: truncated %s messages", budget_cut)

        # 预算削减后重新估算
        if session.estimate_tokens() < threshold * 0.85:
            return  # 削减后 token 够用，无需进一步压缩

        # ── Layer 2: Snip ──
        # 去掉最老的消息（保留 keep 条最新 + system 摘要前缀）
        total = len(session.messages)
        if total > keep * 3:
            # 只保留中间关键消息 + 最新 keep 条
            snip_count = total - keep * 2
            snipped = session.messages[:snip_count]
            recent = session.messages[snip_count:]

            # 从被裁剪的消息中提取关键信息（非 LLM，仅规则）
            key_info: list[str] = []
            for m in snipped:
                if m.role == "tool" and m.name:
                    result_preview = (m.content or "")[:200]
                    if "Error" in result_preview or "error" in result_preview:
                        key_info.append(f"⚠️ 工具 '{m.name}' 执行出错: {result_preview[:100]}")
                elif m.role == "assistant" and m.tool_calls:
                    tool_names = [
                        tc.get("function", {}).get("name", "?")
                        for tc in (m.tool_calls or [])
                    ]
                    key_info.append(f"🔧 调用了: {', '.join(tool_names)}")
                elif m.role == "user" and m.content:
                    key_info.append(f"👤 用户: {(m.content or '')[:100]}")

            # 裁剪后的摘要头
            snipped_summary = (
                f"[上下文裁剪: 省略了 {len(snipped)} 条早期消息]\n"
                + "\n".join(key_info[-20:])  # 最多保留 20 条关键信息
            )
            session.messages = [
                LLMMessage(role="system", content=snipped_summary)
            ] + recent
            logger.info("Layer 2 Snip: removed %s messages, kept %s", len(snipped), len(recent))

        if session.estimate_tokens() < threshold * 0.85:
            return

        # ── Layer 3: Full LLM Compact ──
        # 最昂贵的手段：调用 LLM 生成摘要
        to_compress = session.messages[:-keep]
        recent = session.messages[-keep:]

        def _line_for_summary(m: LLMMessage) -> str:
            if m.role == "tool":
                name = m.name or "tool"
                body = (m.content or "")[:600]
                return f"[tool:{name}] {body}"
            if m.role == "assistant" and m.tool_calls:
                names = [
                    tc.get("function", {}).get("name", "?")
                    for tc in (m.tool_calls or [])
                ]
                text = (m.content or "").strip()[:200]
                return f"[assistant] called {', '.join(names)}. {text}".strip()
            body = (m.content or "")[:400]
            return f"[{m.role}] {body}"

        summary_prompt = LLMMessage(
            role="user",
            content=(
                "请将以下对话历史总结为一段简洁的摘要（中英文均可），"
                "保留关键决策、工具执行结果、错误信息、重要上下文。\n\n"
                + "\n".join(_line_for_summary(m) for m in to_compress)
            ),
        )
        summary_messages = [
            LLMMessage(role="system", content="你是一个对话摘要器。只需输出摘要，不要添加额外评论。"),
            summary_prompt,
        ]

        try:
            resp = await llm_router.chat(summary_messages, temperature=0.3, max_tokens=1000)
            # 替换旧消息为一条摘要
            session.messages = [
                LLMMessage(role="system", content=f"[对话历史摘要]\n{resp.content}")
            ] + recent
            logger.info("Layer 3 Full Compact: %s→1 summary (session %s)", len(to_compress), session.session_id)
        except Exception as e:
            logger.warning("Compaction failed: %s — falling back to snip", e)
            # 兜底：直接裁剪
            session.messages = session.messages[-keep * 2:]
