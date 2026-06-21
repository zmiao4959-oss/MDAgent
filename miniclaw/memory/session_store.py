"""
memory/session_store.py — SQLite-based session persistence with FTS5 search.

Replaces the legacy JSON-file-per-session approach (O(n) scan on every
resolve) with an indexed SQLite store.  Backward-compatible: auto-imports
existing JSON sessions on first open.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import WORKSPACE_DIR
from ..logger import get_logger

logger = get_logger(__name__)

DB_FILENAME = "sessions.db"
_DEFAULT_DB_PATH = WORKSPACE_DIR.parent / DB_FILENAME


def _now() -> float:
    return time.time()


class SessionStore:
    """SQLite-backed session storage with full-text search."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path or _DEFAULT_DB_PATH)
        self._conn: Optional[sqlite3.Connection] = None

    # ── lifecycle ────────────────────────────────────────────────

    def open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SessionStore not open — call .open() first")
        return self._conn

    # ── schema ───────────────────────────────────────────────────

    def _migrate(self) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id  TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                channel     TEXT NOT NULL DEFAULT '',
                account_id  TEXT NOT NULL DEFAULT '',
                messages    TEXT NOT NULL DEFAULT '[]',
                metadata    TEXT NOT NULL DEFAULT '{}',
                created_at  REAL NOT NULL,
                last_active REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_chat
                ON sessions(chat_id, last_active DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_channel
                ON sessions(channel, last_active DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                USING fts5(session_id, chat_id, content, tokenize='unicode61');
            """
        )
        self.conn.commit()
        # 从旧 JSON 目录导入（仅首次）
        self._maybe_import_json_sessions()

    # ── JSON migration ───────────────────────────────────────────

    def _maybe_import_json_sessions(self) -> None:
        """Import legacy JSON session files on first run (idempotent)."""
        c = self.conn
        row = c.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
        if row and row["cnt"] > 0:
            return

        legacy_dir = WORKSPACE_DIR / "sessions"
        if not legacy_dir.is_dir():
            return

        imported = 0
        for path in legacy_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            sid = data.get("session_id") or path.stem.replace("_", ":")
            cid = data.get("chat_id") or ""
            ch = data.get("metadata", {}).get("channel", "")
            aid = data.get("metadata", {}).get("account_id", "")
            msgs = json.dumps(data.get("messages", []), ensure_ascii=False)
            meta = json.dumps(data.get("metadata", {}), ensure_ascii=False)
            ca = float(data.get("created_at", _now()))
            la = float(data.get("last_active", _now()))

            c.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, chat_id, channel, account_id, messages, metadata, created_at, last_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, cid, ch, aid, msgs, meta, ca, la),
            )
            imported += 1

        if imported:
            self.conn.commit()
            logger.info("Imported %s legacy JSON sessions into SQLite", imported)

    # ── CRUD ─────────────────────────────────────────────────────

    def save(self, session_id: str, chat_id: str, *,
             channel: str = "", account_id: str = "",
             messages: List[Dict[str, Any]], metadata: Dict[str, Any],
             created_at: Optional[float] = None,
             last_active: Optional[float] = None) -> None:
        now = _now()
        c = self.conn
        c.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, chat_id, channel, account_id, messages, metadata, created_at, last_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                chat_id,
                channel,
                account_id,
                json.dumps(messages, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False),
                created_at or now,
                last_active or now,
            ),
        )
        # Update FTS index
        text_content = " ".join(
            (m.get("content") or "") for m in messages if m.get("role") in ("user", "assistant")
        )
        c.execute(
            "INSERT OR REPLACE INTO sessions_fts(session_id, chat_id, content) VALUES (?, ?, ?)",
            (session_id, chat_id, text_content),
        )
        self.conn.commit()

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row["session_id"],
            "chat_id": row["chat_id"],
            "channel": row["channel"],
            "account_id": row["account_id"],
            "messages": json.loads(row["messages"]),
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "last_active": row["last_active"],
        }

    def delete(self, chat_id: str) -> bool:
        c = self.conn
        # 先找出受影响的 session_id
        sids = [r["session_id"] for r in
                c.execute("SELECT session_id FROM sessions WHERE chat_id = ?", (chat_id,)).fetchall()]
        c.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
        for sid in sids:
            c.execute("DELETE FROM sessions_fts WHERE session_id = ?", (sid,))
        self.conn.commit()
        return len(sids) > 0

    def resolve_by_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recently active session for a chat_id — O(log n)."""
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE chat_id = ? ORDER BY last_active DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row["session_id"],
            "chat_id": row["chat_id"],
            "channel": row["channel"],
            "account_id": row["account_id"],
            "messages": json.loads(row["messages"]),
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "last_active": row["last_active"],
        }

    def list_by_channel(self, channel: str, *,
                        limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT chat_id, session_id, last_active, messages, metadata
               FROM sessions WHERE channel = ?
               ORDER BY last_active DESC LIMIT ? OFFSET ?""",
            (channel, limit, offset),
        ).fetchall()
        out = []
        for r in rows:
            msgs = json.loads(r["messages"])
            preview = ""
            for m in reversed(msgs):
                if m.get("role") == "user" and (m.get("content") or "").strip():
                    preview = (m.get("content") or "")[:80].replace("\n", " ")
                    break
            out.append({
                "chat_id": r["chat_id"],
                "session_id": r["session_id"],
                "last_active": r["last_active"],
                "preview": preview,
                "message_count": len(msgs),
                "total_tokens": json.loads(r["metadata"]).get("total_tokens", 0),
            })
        return out

    def search(self, query: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text search across session messages (FTS5)."""
        rows = self.conn.execute(
            """SELECT s.* FROM sessions s
               JOIN sessions_fts f ON s.session_id = f.session_id
               WHERE sessions_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "chat_id": r["chat_id"],
                "channel": r["channel"],
                "last_active": r["last_active"],
                "preview": (json.loads(r["messages"])[-1].get("content", "") if json.loads(r["messages"]) else "")[:120],
            }
            for r in rows
        ]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


# ── module-level convenience ────────────────────────────────────

_store: Optional[SessionStore] = None


def get_store(db_path: Optional[Path] = None) -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(db_path)
        _store.open()
    return _store


def close_store() -> None:
    global _store
    if _store:
        _store.close()
        _store = None
