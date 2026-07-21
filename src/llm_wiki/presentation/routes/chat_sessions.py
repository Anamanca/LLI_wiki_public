"""Chat session routes backed by ChatSessionRepository.

These routes are mounted unconditionally because the chat UI depends on them.
They replace the stub implementations in ``stubs.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from llm_wiki.application.ports.repositories.chat_session_repository import (
    ChatMessage,
    ChatSessionRepository,
)
from llm_wiki.presentation.dependencies import container


router = APIRouter()


def get_chat_session_repo() -> ChatSessionRepository:
    return container.chat_session_repo()


class ChatMessagePayload(BaseModel):
    role: str
    content: str


class ChatSessionCreatePayload(BaseModel):
    title: Optional[str] = None


class ChatSessionUpdatePayload(BaseModel):
    messages: list[ChatMessagePayload]
    title: Optional[str] = None


class ChatSessionMetaResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    messages: list[dict]
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_session(session) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        messages=[{"role": m.role, "content": m.content} for m in session.messages],
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


def _serialize_meta(session) -> ChatSessionMetaResponse:
    return ChatSessionMetaResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        message_count=len(session.messages),
    )


@router.get("/chat/sessions", response_model=list[ChatSessionMetaResponse])
async def list_chat_sessions(
    repo: ChatSessionRepository = Depends(get_chat_session_repo),
):
    sessions = await repo.list_sessions()
    return [_serialize_meta(s) for s in sessions]


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_chat_session(
    payload: ChatSessionCreatePayload,
    repo: ChatSessionRepository = Depends(get_chat_session_repo),
):
    session = await repo.create(title=payload.title)
    return _serialize_session(session)


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    repo: ChatSessionRepository = Depends(get_chat_session_repo),
):
    session = await repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _serialize_session(session)


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_chat_session(
    session_id: str,
    payload: ChatSessionUpdatePayload,
    repo: ChatSessionRepository = Depends(get_chat_session_repo),
):
    session = await repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.messages = [
        ChatMessage(role=m.role, content=m.content) for m in payload.messages
    ]
    # Auto-title from the first user message when the session still has the
    # default placeholder title.
    if payload.title is not None:
        session.title = payload.title
    elif session.title in ("New Chat", "Untitled", "", None):
        first_user = next(
            (m for m in payload.messages if m.role == "user"), None
        )
        if first_user:
            session.title = first_user.content[:60]
    await repo.save(session)
    return _serialize_session(session)


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    repo: ChatSessionRepository = Depends(get_chat_session_repo),
):
    deleted = await repo.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}
