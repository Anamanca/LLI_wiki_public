"""File-based implementation of ChatSessionRepository.

Sessions are stored as JSON files under ``CHAT_HISTORY_DIR`` (default
``/data/chat-history``).  Each file is named ``{session_id}.json``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from llm_wiki.application.ports.repositories.chat_session_repository import (
    ChatMessage,
    ChatSession,
    ChatSessionRepository,
)
from llm_wiki.shared.datetime_utils import now

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _now() -> datetime:
    return now()


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ChatSessionFileRepository(ChatSessionRepository):
    def __init__(self, base_dir: str | None = None):
        self._base_dir = Path(base_dir or os.getenv("CHAT_HISTORY_DIR", "/data/chat-history"))
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise ValueError(f"Invalid session id: {session_id}")
        return self._base_dir / f"{session_id}.json"

    def _session_to_dict(self, session: ChatSession) -> dict:
        return {
            "id": session.id,
            "title": session.title,
            "messages": [{"role": m.role, "content": m.content} for m in session.messages],
            "created_at": _to_iso(session.created_at),
            "updated_at": _to_iso(session.updated_at),
        }

    def _dict_to_session(self, data: dict) -> ChatSession:
        return ChatSession(
            id=data["id"],
            title=data.get("title", "Chat"),
            messages=[
                ChatMessage(role=m["role"], content=m["content"]) for m in data.get("messages", [])
            ],
            created_at=_from_iso(data["created_at"]),
            updated_at=_from_iso(data["updated_at"]),
        )

    async def list_sessions(self) -> list[ChatSession]:
        sessions: list[ChatSession] = []
        for path in self._base_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(self._dict_to_session(data))
            except Exception:
                continue
        # Newest activity first so the sidebar shows recent chats at the top.
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    async def get_by_id(self, session_id: str) -> ChatSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._dict_to_session(data)
        except Exception:
            return None

    async def create(self, title: str | None = None) -> ChatSession:
        session_id = uuid.uuid4().hex[:12]
        now = _now()
        session = ChatSession(
            id=session_id,
            title=title or "New Chat",
            messages=[],
            created_at=now,
            updated_at=now,
        )
        await self.save(session)
        return session

    async def save(self, session: ChatSession) -> ChatSession:
        session.updated_at = _now()
        path = self._path(session.id)
        path.write_text(
            json.dumps(self._session_to_dict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return session

    async def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True
