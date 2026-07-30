import json
import os
import tempfile

import pytest

from llm_wiki.application.ports.repositories.chat_session_repository import (
    ChatMessage,
)
from llm_wiki.infrastructure.persistence.file import ChatSessionFileRepository


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ChatSessionFileRepository(tmpdir)


@pytest.mark.chatsessions
async def test_create_returns_session_with_id_and_empty_messages(repo):
    session = await repo.create()
    assert session.id
    assert session.title == "New Chat"
    assert session.messages == []
    assert session.created_at
    assert session.updated_at


@pytest.mark.chatsessions
async def test_create_with_title(repo):
    session = await repo.create(title="Custom Title")
    assert session.title == "Custom Title"


@pytest.mark.chatsessions
async def test_save_persists_messages(repo):
    session = await repo.create()
    session.messages = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
    ]
    saved = await repo.save(session)

    # Read raw file to verify persistence
    path = os.path.join(repo._base_dir, f"{session.id}.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        raw = json.loads(f.read())
    assert raw["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert saved.updated_at >= saved.created_at


@pytest.mark.chatsessions
async def test_get_by_id_returns_saved_session(repo):
    session = await repo.create()
    session.messages = [ChatMessage(role="user", content="question")]
    await repo.save(session)

    fetched = await repo.get_by_id(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.messages[0].role == "user"
    assert fetched.messages[0].content == "question"


@pytest.mark.chatsessions
async def test_get_by_id_missing_returns_none(repo):
    assert await repo.get_by_id("doesnotexist") is None


@pytest.mark.chatsessions
async def test_get_by_id_invalid_id_raises(repo):
    with pytest.raises(ValueError):
        await repo.get_by_id("../etc/passwd")


@pytest.mark.chatsessions
async def test_list_sessions_returns_all(repo):
    s1 = await repo.create(title="One")
    s2 = await repo.create(title="Two")
    sessions = await repo.list_sessions()
    ids = {s.id for s in sessions}
    assert s1.id in ids
    assert s2.id in ids


@pytest.mark.chatsessions
async def test_list_sessions_skips_corrupt_files(repo):
    session = await repo.create()
    corrupt_path = os.path.join(repo._base_dir, "bad.json")
    with open(corrupt_path, "w", encoding="utf-8") as f:
        f.write("not json")
    sessions = await repo.list_sessions()
    assert any(s.id == session.id for s in sessions)
    assert len(sessions) == 1


@pytest.mark.chatsessions
async def test_delete_removes_session(repo):
    session = await repo.create()
    deleted = await repo.delete(session.id)
    assert deleted is True
    assert await repo.get_by_id(session.id) is None


@pytest.mark.chatsessions
async def test_delete_missing_returns_false(repo):
    assert await repo.delete("missing") is False


@pytest.mark.chatsessions
async def test_constructor_uses_env_var(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CHAT_HISTORY_DIR", tmpdir)
        repo = ChatSessionFileRepository()
        session = await repo.create()
        assert os.path.exists(os.path.join(tmpdir, f"{session.id}.json"))
