import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import HTTPException

from llm_wiki.application.ports.repositories.chat_session_repository import (
    ChatMessage,
    ChatSession,
)
from llm_wiki.presentation.routes.chat_sessions import update_chat_session


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    now = datetime.now(UTC)
    session = ChatSession(
        id="sess-1",
        title="New Chat",
        messages=[],
        created_at=now,
        updated_at=now,
    )
    repo.get_by_id.return_value = session
    repo.save = AsyncMock(return_value=session)
    return repo


@pytest.mark.asyncio
async def test_update_chat_session_auto_titles_from_first_user_message(mock_repo):
    payload = type("P", (), {
        "messages": [
            ChatMessage(role="user", content="What is retrieval augmented generation?"),
            ChatMessage(role="assistant", content="It is..."),
        ],
        "title": None,
    })()
    result = await update_chat_session("sess-1", payload, mock_repo)
    assert result.title == "What is retrieval augmented generation?"


@pytest.mark.asyncio
async def test_update_chat_session_respects_explicit_title(mock_repo):
    payload = type("P", (), {
        "messages": [
            ChatMessage(role="user", content="Question"),
        ],
        "title": "Custom Title",
    })()
    result = await update_chat_session("sess-1", payload, mock_repo)
    assert result.title == "Custom Title"


@pytest.mark.asyncio
async def test_update_chat_session_keeps_existing_custom_title(mock_repo):
    mock_repo.get_by_id.return_value.title = "Existing Title"
    payload = type("P", (), {
        "messages": [
            ChatMessage(role="user", content="New question"),
        ],
        "title": None,
    })()
    result = await update_chat_session("sess-1", payload, mock_repo)
    assert result.title == "Existing Title"


@pytest.mark.asyncio
async def test_update_chat_session_missing_returns_404(mock_repo):
    mock_repo.get_by_id.return_value = None
    payload = type("P", (), {
        "messages": [],
        "title": None,
    })()
    with pytest.raises(HTTPException, match="Session not found"):
        await update_chat_session("missing", payload, mock_repo)
